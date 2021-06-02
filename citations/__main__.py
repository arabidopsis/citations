# pylint: disable=unused-import
from . import citations  # noqa:
from .cli import cli

if __name__ == "__main__":
    cli.main(prog_name="citations")
