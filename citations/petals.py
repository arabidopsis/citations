import click


def update_from_petals(db):
    import pandas as pd
    from pymongo import MongoClient
    from sqlalchemy import select

    click.secho("remember to ssh into croppal", fg="yellow", bold=True)
    c = MongoClient("mongodb://127.0.0.1:27018/personnel")
    mdb = c.get_default_database()
    all_pubs = list(
        mdb.publications.find({}, projection=["title", "doi", "pubmed", "year"])
    )
    all_pubs = pd.DataFrame.from_records(all_pubs)
    all_pubs = all_pubs.drop("_id", axis="columns")
    all_pubs.year = all_pubs.year.astype(int)
    all_pubs.ncitations = -1
    all_pubs = all_pubs[~all_pubs.pubmed.isna()]

    p = db.publications
    q = select([p.c.pubmed]).where(p.c.pubmed.in_(all_pubs.pubmed.to_list()))

    done = {r.pubmed for r in db.execute(q)}

    all_pubs = all_pubs[~all_pubs.pubmed.isin(done)]
    click.secho(f"found {len(all_pubs)} new publications", fg="green")

    all_pubs.to_sql("publications", con=db.engine, index=False, if_exists="append")
