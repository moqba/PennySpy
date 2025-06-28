# PennySpy
Bank scrapper to fetch and download your transaction history files

## RBC bank
### install
`pip install git+https://github.com/moqba/PennySpy`

### usage
The following env variables are required :
```dotenv
FETCHER_USER="rbc_username"
FETCHER_PASSWORD="rbc_password"
```
### 2FA
When prompting connection user has 5 minutes to accept the 2FA on mobile device to allow the script to connect.

### Example python script
```python
from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import Software, AccountInfo, Include

bank = RBCBank()
bank.get_session_cookies()
bank.download_transactions(software=Software.CSV, account_info=AccountInfo.PRIMARY_CHECKING, include=Include.ALL_OPERATIONS)
```