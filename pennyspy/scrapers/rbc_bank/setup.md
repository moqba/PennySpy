# Usage
The scraper is only compatible with users using mobile 2FA. If your RBC is setup with SMS 2FA, there is no current integration for this method.
When prompting connection user has 5 minutes to accept the 2FA on mobile device to allow the script to connect.

Regardless of the installation method, the following env variables are required :
```dotenv
PENNYSPY_RBCU="rbc_username"
PENNYSPY_RBCP="rbc_password"
```
it is recommended to make an `.env` file containing these.

## RBC API
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

# Python call
User can invoke the download_transactions method with the desired acounting software, account and operations to include. View enumeration for available options.
```python
from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import Software, AccountInfo, Include

bank = RBCBank()
bank.get_session_cookies()
bank.download_transactions(software=Software.CSV, account_info=AccountInfo.PRIMARY_CHECKING, include=Include.ALL_OPERATIONS)
```
