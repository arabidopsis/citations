import time
from io import BytesIO
from typing import Any, Dict, Iterable

import requests


ESEARCH2 = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def fetchncbimeta(pubmed, email, session=None, headers=None):
    params = dict(db="pubmed", retmode="xml", id=pubmed, email=email)
    resp = (session or requests).get(EFETCH, params=params, headers=headers)

    return resp


# pylint: disable=too-many-locals
def fetchncbi(
    pubmed: str, email: str, full=True, session=None, headers=None
) -> Iterable[Dict[str, Any]]:
    from lxml import etree as ET

    resp = fetchncbimeta(pubmed, email, session=session, headers=headers)
    try:
        ipt = BytesIO(resp.content)
        tree = ET.parse(ipt)
        error = tree.getroot().tag
        if error == "ERROR":  # no id
            return
        articles = tree.findall("PubmedArticle")
        for pm_article in articles:
            # for citation in citations:
            citation = pm_article.find("MedlineCitation")
            pmid = citation.findtext("PMID")
            article = citation.find("Article")

            title = article.findtext("ArticleTitle")
            journal = article.find("Journal")

            year = journal.findtext("JournalIssue/PubDate/Year")
            year = year or journal.findtext("JournalIssue/PubDate/MedlineDate")
            year = year.strip()[:4]

            year = int(year)

            ids = pm_article.findall(
                "PubmedData/ArticleIdList/ArticleId[@IdType='doi']"
            )
            pmc = pm_article.findall(
                "PubmedData/ArticleIdList/ArticleId[@IdType='pmc']"
            )
            doi = ids[0].text if ids else None
            pmc = pmc[0].text if pmc else None
            # if doi: doi = 'http://dx.doi.org/'+doi
            if not full:
                yield {
                    "pubmed": pmid,
                    "year": year,
                    "title": title,
                    "doi": doi,
                    "pmc": pmc,
                }
                continue
                # doi = [i.text for i in ids if i.attrib.get('IdType') == 'doi']
                # if doi: doi=doi[0]
                # else: doi=''
            name = journal.findtext("ISOAbbreviation", None) or journal.findtext(
                "Title", ""
            )
            volume = journal.findtext("JournalIssue/Volume")
            issue = journal.findtext("JournalIssue/Issue")
            abstract = article.findtext("Abstract/AbstractText")
            authors = article.findall("AuthorList/Author")
            pages = article.findtext("Pagination/MedlinePgn")

            # elementtree tries to encode everything as ascii
            # or if that fails it leaves the string alone
            def findaffiliation(node):
                return node.findtext("AffiliationInfo/Affiliation") or ""

            def aff(node):
                # affiliation = n.findtext('AffiliationInfo/Affiliation')
                for ai in node.xpath(".//AffiliationInfo"):
                    grid = [a.text for a in ai.xpath('.//Identifier[@Source="GRID"]')]
                    isni = [a.text for a in ai.xpath('.//Identifier[@Source="ISNI"]')]
                    affiliation = [a.text for a in ai.xpath(".//Affiliation")]
                    yield dict(
                        grid=grid[0] if grid else None,
                        isni=isni[0] if isni else None,
                        affiliation=affiliation[0],
                    )

            alist = [
                {
                    "lastname": a.findtext("LastName"),
                    "forename": a.findtext("ForeName"),
                    "initials": a.findtext("Initials"),
                    # "affiliation": list(aff(a)),
                    "affiliation": findaffiliation(a),
                    "orcid": " ".join(
                        [o.text for o in a.xpath('.//Identifier[@Source="ORCID"]')]
                    ),
                }
                for a in authors
            ]
            # author = alist[0]
            yield {
                "pubmed": pmid,
                "year": year,
                "title": title,
                "abstract": abstract,
                "authors": alist,
                "journal": name,
                "volume": volume,
                "issue": issue,
                "pages": pages,
                "doi": doi,
                "pmc": pmc
                # 'xml':xml
            }

    finally:
        pass


def ncbi_esearch(
    query: str, email: str, retmax=10000, session=None, headers: dict = None
) -> Dict[str, Any]:

    values = dict(
        db="pubmed", retmode="json", retmax=str(retmax), term=query, email=email
    )
    if len(query) > 300:
        fp = (session or requests).post(ESEARCH2, data=values, headers=headers)
    else:
        fp = (session or requests).get(ESEARCH2, params=values, headers=headers)
    try:
        return fp.json()

    finally:
        fp.close()


def ncbi_fetchdoi(
    doi: str, email: str, sleep=1.0, session=None, headers=None
) -> Iterable[Dict[str, Any]]:

    r = ncbi_esearch(f"{doi}[DOI]", email, session=session, headers=headers)
    if "esearchresult" in r:
        for pmid in r["esearchresult"]["idlist"]:
            if sleep:
                time.sleep(sleep)
            yield from fetchncbi(
                pmid, email, full=True, session=session, headers=headers
            )
