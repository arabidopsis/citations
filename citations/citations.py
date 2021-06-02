import re
import time
from typing import Iterable, Dict, Any
import pandas as pd

import click

from .cli import cli

DOI = re.compile(r"coci => ([^\s]+)$")


def fetch_opennet(doi: str) -> Dict[str, Any]:
    import requests

    r = requests.get(f"https://w3id.org/oc/index/api/v1/citations/{doi}")
    r.raise_for_status()
    return r.json()


def fetch_crossref(doi: str) -> Dict[str, Any]:
    import requests

    r = requests.get(f"https://api.crossref.org/works/{doi}")
    r.raise_for_status()
    m = r.json()
    assert "status" in m and m["status"] == "ok", m
    return m["message"]


def fetch_publications(mongo: str = None) -> pd.DataFrame:
    from pymongo import MongoClient

    if mongo is None:
        mongo = "mongodb://127.0.0.1:27017/personnel"

    c = MongoClient(mongo)
    db = c.get_default_database()
    pubsl = list(
        db.publications.find({}, {"doi": 1, "pubmed": 1, "title": 1, "year": 1})
    )

    pubs = pd.DataFrame.from_records(pubsl)
    pubs.year = pubs.year.astype(int)
    pubs = pubs.sort_values(["year", "title"])
    pubs = pubs.drop("_id", axis="columns")
    pubs["ncitations"] = -1
    return pubs


def citations(doi: str) -> Iterable[str]:
    r = fetch_opennet(doi)
    for d in r:
        m = DOI.match(d["cited"])
        if m and m.group(1) != doi:
            continue
        m = DOI.match(d["citing"])
        if m:
            yield m.group(1)


def citation_df(doi: str) -> pd.DataFrame:

    df = pd.DataFrame({"citedby": list(set(citations(doi)))})
    df["doi"] = doi
    return df


class Db:
    def __init__(self, engine, publications, citations_table, meta_table):
        from sqlalchemy import bindparam, select

        self.pd = pd

        self.engine = engine
        self.publications = publications
        self.citations = citations_table
        self.meta_table = meta_table
        self.select = select
        self.update = (
            publications.update()  # pylint: disable=no-value-for-parameter
            .values({publications.c.ncitations: bindparam("b_ncitations")})
            .where(publications.c.doi == bindparam("b_doi"))
        )

    def count(self, t, q=None) -> int:
        from sqlalchemy import func

        q2 = self.select([func.count()]).select_from(t)
        if q:
            q2 = q2.where(q)
        with self.engine.connect() as conn:
            return conn.execute(q2).fetchone()[0]

    def execute(self, query):
        with self.engine.connect() as conn:
            return conn.execute(query).fetchall()

    def update_citation_count(self, doi, ncitations):
        with self.engine.connect() as con:
            proxy = con.execute(self.update, b_doi=doi, b_ncitations=ncitations)
            assert proxy.rowcount == 1, (doi, proxy.rowcount)

    def update_citations(self, df: pd.DataFrame):
        df.to_sql("citations", con=self.engine, if_exists="append", index=False)

    def fixdoi(self, olddoi: str, newdoi: str):
        p = self.publications
        u = p.update().values({p.c.doi: newdoi}).where(p.c.doi == olddoi)
        with self.engine.connect() as con:
            con.execute(u)

    def todo(self) -> pd.DataFrame:

        return self.pd.read_sql_query(
            self.select([self.publications]).where(self.publications.c.ncitations < 0),
            con=self.engine,
        )

    def npubs(self) -> int:
        return self.count(self.publications)

    def ndone(self) -> int:
        return self.count(self.publications, q=self.publications.c.ncitations >= 0)

    def ncitations(self) -> int:
        return self.count(self.citations)


def initdb() -> Db:
    from sqlalchemy import (
        JSON,
        Boolean,
        Column,
        Integer,
        MetaData,
        String,
        Table,
        create_engine,
        text,
    )

    meta = MetaData()
    Publications = Table(
        "publications",
        meta,
        Column("id", Integer, primary_key=True),
        Column("doi", String(64), index=True),
        Column("pubmed", String(16)),
        Column("title", String(256)),
        Column("year", Integer),
        Column("ncitations", Integer),
    )

    Citations = Table(
        "citations",
        meta,
        Column("id", Integer, primary_key=True),
        Column("doi", String(64), index=True),
        Column("citedby", String(64)),
    )
    Meta = Table(
        "metadata",
        meta,
        Column("id", Integer, primary_key=True),
        Column("doi", String(64), index=True, nullable=False),
        Column("pubmed", String(12)),
        Column("source", String(12), nullable=False),
        Column("status", Integer, nullable=False, server_default=text("0")),
        Column("has_affiliation", Boolean),
        Column("data", JSON),
    )

    engine = create_engine("sqlite:///./citations.db")
    Publications.create(bind=engine, checkfirst=True)
    Citations.create(bind=engine, checkfirst=True)
    Meta.create(bind=engine, checkfirst=True)

    return Db(engine, Publications, Citations, Meta)


def dometadata(db: Db, email: str, sleep=1.0, ntry=4):
    import requests
    from sqlalchemy import null, select
    from tqdm import tqdm

    from .ncbi import ncbi_fetchdoi

    m = db.meta_table
    c = db.citations
    j = c.outerjoin(m, m.c.doi == c.c.citedby)
    q = select([c.c.citedby.distinct()]).select_from(j).where(m.c.doi == null())

    with db.engine.connect() as con:
        todo = {r.citedby for r in con.execute(q)}
    click.secho(f"todo {len(todo)}", fg="blue")

    session = requests.Session()

    def insert(d):
        with db.engine.connect() as conn:
            conn.execute(m.insert(), d)

    with tqdm(todo) as pbar:
        for doi in pbar:
            try:
                data = list(ncbi_fetchdoi(doi, email, sleep, session=session))
                if not data:
                    d = dict(doi=doi, status=-1, source="ncbi")
                    insert(d)
                    continue
                for d in data:
                    has_aff = any(bool(a.get("affiliation")) for a in d["authors"])
                    d = dict(
                        doi=doi,
                        pubmed=d["pubmed"],
                        status=1,
                        source="ncbi",
                        has_affiliation=has_aff,
                        data=d,
                    )
                    insert(d)

                    if sleep:
                        time.sleep(sleep)
            except Exception as e:  # pylint: disable=broad-except
                pbar.write(click.style(f"failed for {doi}: {e}", fg="red"))
                d = dict(doi=doi, status=-2, source="ncbi")
                insert(d)
                ntry -= 1
                if ntry <= 0:
                    raise


def docitations(db: Db, sleep=1.0):
    from requests.exceptions import HTTPError
    from tqdm import tqdm

    todo = db.todo()
    ncitations = db.ncitations()
    click.secho(f"todo: {len(todo)}. Already found {ncitations} citations", fg="yellow")
    added = 0
    mx_exc = 4
    with tqdm(todo.itertuples(), total=len(todo), postfix={"added": 0}) as pbar:
        for row in pbar:
            if not row.doi:
                pbar.write(click.style(f"{row.Index}: no DOI", fg="red"))
                continue
            try:
                doi = fixdoi(row.doi)
                if doi != row.doi:
                    pbar.write(click.style(f"fixing {row.doi} -> {doi}", fg="yellow"))
                    db.fixdoi(row.doi, doi)
                df = citation_df(doi)
                db.update_citation_count(doi, len(df))
                db.update_citations(df)
                added += len(df)
                pbar.set_postfix(added=added)
                if sleep:
                    time.sleep(sleep)
            except HTTPError as e:
                mx_exc -= 1
                if mx_exc <= 0:
                    raise e
                pbar.write(click.style(f"{row.doi}: exception {e}", fg="red"))


def fixdoi(doi: str) -> str:
    doi = doi.replace("%2F", "/")
    for prefix in [
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "https://doi.org/",
        "http://doi.org/",
        "doi:",
    ]:
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    return doi


def fixpubs(pubs: pd.DataFrame) -> pd.DataFrame:

    missing = pubs.doi.isna()
    smissing = missing.sum()
    if smissing:
        click.secho(f"missing {smissing} dois", fg="yellow")

    pubs = pubs[~missing].copy()  # get rid of missing
    pubs["doi"] = pubs.doi.apply(fixdoi)
    pubs = pubs.drop_duplicates(["doi"], ignore_index=True)

    return pubs


@cli.command(name="fixdoi")
def fixdoi_():
    """Fix any incorrect dois."""

    db = initdb()
    df = pd.read_sql_table("publications", con=db.engine)
    df = fixpubs(df)
    db.publications.drop(bind=db.engine)
    db.publications.create(bind=db.engine)
    df.to_sql(  # pylint: disable=no-member
        "publications", con=db.engine, if_exists="append", index=False
    )


@cli.command()
@click.option("--sleep", default=1.0)
@click.option("--mongo")
def scan(sleep, mongo):
    """Scan https://opencitations.net."""
    db = initdb()
    if db.npubs() == 0:
        pubs = fetch_publications(mongo)
        pubs = fixpubs(pubs)

        click.secho(f"found {len(pubs)} publications", fg="green")
        pubs.to_sql(  # pylint: disable=no-member
            "publications", con=db.engine, if_exists="append", index=False
        )

    docitations(db, sleep)


@cli.command()
@click.argument("filename", type=click.Path(dir_okay=False))
def tocsv(filename):
    """Dump citations to FILENAME as CSV."""

    db = initdb()
    df = pd.read_sql_table("citations", con=db.engine)
    df.to_csv(filename, index=False)  # pylint: disable=no-member


@cli.command()
@click.option(
    "--sleep",
    default=1.0,
    help="time to sleep in seconds between requests",
    show_default=True,
)
@click.option(
    "--ntry",
    default=4,
    help="number of retries before failing",
    show_default=True,
)
@click.option(
    "--no-email",
    is_flag=True,
    help="don't send email",
)
@click.argument("email")
def ncbi_metadata(email: str, sleep: float, no_email: bool, ntry: int):
    """Get metadata for citations."""
    from datetime import datetime
    from html import escape

    from .mailer import sendmail

    db = initdb()
    click.secho(f"citations {db.ncitations()}", fg="green")
    start = datetime.now()
    try:
        dometadata(db, email, sleep, ntry=ntry)
        if not no_email:
            sendmail(f"ncbi-metadata done in {datetime.now() - start}", email)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        if not no_email:
            sendmail(
                f"ncbi-metadata <b>failed!</b><br/><pre>{escape(str(e))}</pre>", email
            )
        raise


@cli.command()
@click.argument("email")
@click.argument("message")
def test_email(email, message):
    """Test email."""
    from .mailer import sendmail

    sendmail(message, email)
    click.secho("email sent!", fg="green")


@cli.command()
@click.argument("table")
@click.argument("filename", type=click.Path(dir_okay=False))
def dump(table, filename):
    """Dump citation table to FILENAME as CSV."""

    db = initdb()
    df = pd.read_sql_table(table, con=db.engine)
    df.to_csv(filename, index=False)  # pylint: disable=no-member
