import re

from setuptools import find_packages, setup

V = re.compile(r'^VERSION\s*=\s*"([^"]+)"\s*$', re.M)


def getversion():
    with open("citations/config.py") as fp:
        return V.search(fp.read()).group(1)


req = [f.strip() for f in open("requirements.txt")]

setup(
    name="footprint",
    version=getversion(),
    packages=find_packages(),
    include_package_data=True,
    install_requires=req,
    entry_points="""
        [console_scripts]
        citations=citations.__main__:cli
    """,
)
