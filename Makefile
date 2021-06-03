requirements:
	@pipreqs --print citations | sed 's/==/>=/' | sed 's/bio>/biopython>/' | sort | uniq > requirements.txt

pre-commit:
	pre-commit run --all-files

pylint:
	pylint citations/

install:
	pip install --editable .

version:
	git rev-parse HEAD | cut -c1-7
