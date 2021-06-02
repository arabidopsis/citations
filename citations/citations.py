import re
import time

import click

from .cli import cli

DOI = re.compile(r"coci => ([^\s]+)$")


def fetch_opennet(doi):
    import requests

    r = requests.get(f"https://w3id.org/oc/index/api/v1/citations/{doi}")
    r.raise_for_status()
    return r.json()


def fetch_crossref(doi):
    import requests

    r = requests.get(f"https://api.crossref.org/works/{doi}")
    r.raise_for_status()
    m = r.json()
    assert "status" in m and m["status"] == "ok", m
    return m["message"]


def fetch_publications(mongo=None):
    import pandas as pd
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


def citations(doi):
    r = fetch_opennet(doi)
    for d in r:
        m = DOI.match(d["cited"])
        if m and m.group(1) != doi:
            continue
        m = DOI.match(d["citing"])
        if m:
            yield m.group(1)


def citation_df(doi):
    import pandas as pd

    df = pd.DataFrame({"citedby": list(set(citations(doi)))})
    df["doi"] = doi
    return df


class Db:
    def __init__(self, engine, publications, citations_table):
        import pandas
        from sqlalchemy import bindparam, select

        self.pd = pandas

        self.engine = engine
        self.publications = publications
        self.citations = citations_table
        self.select = select
        self.update = (
            publications.update()  # pylint: disable=no-value-for-parameter
            .values({publications.c.ncitations: bindparam("b_ncitations")})
            .where(publications.c.doi == bindparam("b_doi"))
        )

    def count(self, t, q=None):
        from sqlalchemy import func

        q2 = self.select([func.count()]).select_from(t)
        if q:
            q2 = q2.where(q)
        with self.engine.connect() as conn:
            return conn.execute(q2).fetchone()[0]

    def update_citation_count(self, doi, ncitations):
        with self.engine.connect() as con:
            proxy = con.execute(self.update, b_doi=doi, b_ncitations=ncitations)
            assert proxy.rowcount == 1, (doi, proxy.rowcount)

    def update_citations(self, df):
        df.to_sql("citations", con=self.engine, if_exists="append", index=False)

    def fixdoi(self, olddoi, newdoi):
        p = self.publications
        u = p.update().values({p.c.doi: newdoi}).where(p.c.doi == olddoi)
        with self.engine.connect() as con:
            con.execute(u)

    def todo(self):

        return self.pd.read_sql_query(
            self.select([self.publications]).where(self.publications.c.ncitations < 0),
            con=self.engine,
        )

    def npubs(self):
        return self.count(self.publications)

    def ndone(self):
        return self.count(self.publications, q=self.publications.c.ncitations >= 0)

    def ncitations(self):
        return self.count(self.citations)


def initdb():
    from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

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

    engine = create_engine("sqlite:///./citations.db")
    Publications.create(bind=engine, checkfirst=True)
    Citations.create(bind=engine, checkfirst=True)

    return Db(engine, Publications, Citations)


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


def fixdoi(doi):
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


def fixpubs(pubs):

    missing = pubs.doi.isna()
    smissing = missing.sum()
    if smissing:
        click.secho(f"missing {smissing} dois", fg="yellow")

    pubs = pubs[~missing].copy()  # get rid of missing
    pubs["doi"] = pubs.doi.apply(fixdoi)
    pubs = pubs.drop_duplicates(["doi"], ignore_index=True)

    return pubs


@cli.group(help=click.style("citation commands", fg="magenta"))
def cite():
    pass


@cite.command(name="fixdoi")
def fixdoi_():
    import pandas as pd

    db = initdb()
    df = pd.read_sql_table("publications", con=db.engine)
    df = fixpubs(df)
    db.publications.drop(bind=db.engine)
    db.publications.create(bind=db.engine)
    df.to_sql(  # pylint: disable=no-member
        "publications", con=db.engine, if_exists="append", index=False
    )


@cite.command()
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


@cite.command()
@click.argument("filename", type=click.Path(dir_okay=False))
def tocsv(filename):
    """Dump citations to FILENAME as CSV."""
    import pandas as pd

    db = initdb()
    df = pd.read_sql_table("citations", con=db.engine)
    df.to_csv(filename, index=False)  # pylint: disable=no-member
