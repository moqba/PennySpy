# PennySpy
Bank scrapper to fetch and download your transaction history files

## RBC bank
### install
`pip install git+https://github.com/moqba/PennySpy`

### usage
The following env variables are required :
```dotenv
PENNYSPY_RBCU="rbc_username"
PENNYSPY_RBCP="rbc_password"
```
it is recommended to make an `.env` file containing these.
### 2FA
When prompting connection user has 5 minutes to accept the 2FA on mobile device to allow the script to connect.

### Run directly from python
```python
from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import Software, AccountInfo, Include

bank = RBCBank()
bank.get_session_cookies()
bank.download_transactions(software=Software.CSV, account_info=AccountInfo.PRIMARY_CHECKING, include=Include.ALL_OPERATIONS)
```

# API
The api will use the port defined as an env variable `PENNYSPY_PORT`

user can launch the API after installing the python package as such :
```shell
pennyspy_api
```

# Docker
Pennyspy api is also available as a Docker image ready to be run in your own custom environment.
## Launch container using Docker Compose
You can use the `docker-compose.yml` file included in the repository to run the latest stable version of the API.
To create and run the container, you should use an `.env` file for your credentials or pass directly the credentials under the docker-compose file:
```shell
docker compose up --detach
```

## Launch container using docker command
It is possible, alternatively, to use docker using this command.
```shell
docker run --restart=unless-stopped -d -p 5056:5056 -v YOUR/PATH/TO/DATA:/pennyspy --name pennyspy moqba/pennyspy:latest
```


## RBC
#### `POST rbc/scrape`

Initiate a transaction download for a specified RBC account and software format.
The body is in JSON.

**Request Body:**

| Field         | Type      | Description                                                        | Allowed Values                                                                         |
|---------------|-----------|--------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| software      | string    | Target software format for export                                  | QUICKEN, MAKISOFT, QUICKBOOKS, MONEY, MAKISOFT_COMPTABILITY, SIMPLY_ACCOUNTING, CSV    |
| account_info  | string    | Account(s) to include                                              | A, B, VALL, C001, C002
| include       | string    | Which operations to include                                        | N, A

**Options:**


### Software

| Name                   | Value             | Extension |
|------------------------|-------------------|-----------|
| QUICKEN                | "QUICKEN"         | .ofx      |
| MAKISOFT               | "MAKISOFT"        | .afx      |
| QUICKBOOKS             | "QUICKBOOKS"      | .qbo      |
| MONEY                  | "MONEY"           | .ofx      |
| MAKISOFT_COMPTABILITY  | "MAKISOFTB"       | .afx      |
| SIMPLY_ACCOUNTING      | "SIMPLYACCOUNTING"| .aso      |
| CSV                    | "EXCEL"           | .csv      |

### AccountInfo

| Name                | Value    |
|---------------------|----------|
| ALL_ACCOUNTS        | "A"      |
| CHECKING_ACCOUNTS   | "B"      |
| CREDIT_ACCOUNTS     | "VALL"   |
| PRIMARY_CHECKING    | "C001"   |
| SECONDARY_CHECKING  | "C002"   |

### Include

| Name            | Value |
|-----------------|-------|
| NEW_OPERATIONS  | "N"   |
| ALL_OPERATIONS  | "A"   |

---