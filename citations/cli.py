import click
from click_didyoumean import DYMGroup

from .config import VERSION


@click.group(cls=DYMGroup, epilog=click.style("Citation commands\n", fg="magenta"))
@click.version_option(VERSION)
def cli():
    pass
