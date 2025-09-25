# PennySpy
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Canadian bank transaction scraper with API endpoints for automated retrieval of transaction history. Can be self-hosted on your computer directly via a Docker image or accessed directly through Python for seamless integration.
# Supported banks
- RBC bank

# Installation
## Docker
Pennyspy api is also available as a Docker image ready to be run in your own custom environment.
### Launch container using Docker Compose
You can use the `docker-compose.yml` file included in the repository to run the latest stable version of the API.
To create and run the container, you should use an `.env` file for your credentials or pass directly the credentials under the `docker-compose.yml` file:
```shell
docker compose up --detach
```
for required env variables, view the [setup](#Setup) for the bank of choice.

### Launch container using docker command
It is possible, alternatively, to use docker using this command.
```shell
docker run --restart=unless-stopped -d -p 5056:5056 -v YOUR/PATH/TO/DATA:/pennyspy --name pennyspy moqba/pennyspy:latest
```

## Python package
Pennyspy can be installed a python package :  
`pip install git+https://github.com/moqba/PennySpy`  
This allows you to run the scraper directly in Python :
```python
# This example is for RBC bank scraper, for more details about RBC setup consult the RBC bank section
from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import Software, AccountInfo, Include

bank = RBCBank()
bank.get_session_cookies()
bank.download_transactions(software=Software.CSV, account_info=AccountInfo.PRIMARY_CHECKING, include=Include.ALL_OPERATIONS)
```

Or start the [API service](#API) after installing the Python package :
```shell
pennyspy_api
```


# API
The main API service supports all banks.
It runs on the port 5056 by default, user can modify the port by setting `PENNYSPY_PORT` env variable.
The Docker image starts the API service automatically.
See the bank’s setup page for API call details.


# Setup
For RBC bank setup and API details, consult the [RBC setup guide](pennyspy/scrapers/rbc_bank/setup.md).



