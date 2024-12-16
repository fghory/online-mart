(Will be updated and reformatted soon)

Test Cases
Uses pytest for unit testing
Uses Docker Compose Overrides for Testing
    docker-compose.test.yml for defining test related overrides
Merge docker compose and docker compose test files, applying overrides for testing
    docker compose up -d
    docker compose -f compose.test.yml up -d
Kafka functionality is mocked using unittest.mock (or pytest-mock) during tests
Implements Test Coverage Analysis using pytest-cov    
Bring down the test environment to free up resources
    docker-compose -f docker-compose.yml -f docker-compose.test.yml down

poetry add --dev pytest-cov
poetry run pytest --cov=app tests/
    Runs all the test cases in test_main.py inside tests folder
    Generates summry report on test coverage. Example table below:
        
---------- coverage: platform linux, python 3.12.7-final-0 -----------
Name              Stmts   Miss  Cover
-------------------------------------
app/__init__.py       0      0   100%
app/db.py            11      3    73%
app/main.py         156     45    71%
app/models.py        38      0   100%
app/settings.py      11      2    82%
app/user_pb2.py      16      7    56%
app/utils.py         68     15    78%
-------------------------------------
TOTAL               300     72    76%


poetry run pytest --cov=app --cov-report=html tests/
    Running this command in bash inside service container:
        Runs all the test cases in test_main.py inside tests folder
        Generate htmlcov folder containing index.html file 
        index.html file contains detailed report on which statements are run or nor alognwith summary table