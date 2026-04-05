from pathlib import Path

import pandas as pd
from ofxtools.Parser import OFXTree
from datetime import date
from logging import getLogger

logger = getLogger(__name__)


def filter_qfx(input_file, output_file, start_date: date, end_date: date):
    parser = OFXTree()
    parser.parse(input_file)
    ofx = parser.convert()

    total_removed = 0

    for msg_set in ofx.statements:
        for stmt in msg_set.statements:
            acct_id = stmt.bankacctfrom.acctid if hasattr(stmt, 'bankacctfrom') else stmt.ccacctfrom.acctid

            tran_list = stmt.banktranlist
            if not tran_list:
                continue

            orig_count = len(tran_list.transactions)

            filtered = [
                tx for tx in tran_list.transactions
                if start_date <= tx.dtposted.date() <= end_date
            ]

            tran_list.transactions = filtered
            removed = orig_count - len(filtered)
            total_removed += removed

            logger.info(f"Account {acct_id}: Kept {len(filtered)} transactions (Removed {removed})")

            if filtered:
                stmt.dtstart = min(tx.dtposted for tx in filtered)
                stmt.dtend = max(tx.dtposted for tx in filtered)

    with open(output_file, 'wb') as f:
        f.write(ofx.to_etree().to_xml())

    logger.info(f"--- Process complete. Total transactions removed: {total_removed} ---")


def get_column_possible_values(csv_path: str | Path, column_name: str) -> set[str]:
    try:
        df = pd.read_csv(csv_path)
        if column_name in df.columns:
            unique_values = set(df[column_name].unique())
            return unique_values
        else:
            raise ValueError(f"Error: Column '{column_name}' not found in {csv_path}.")
    except FileNotFoundError:
        return "Error: File not found. Please check the path."
    except Exception as e:
        return f"An unexpected error occurred: {e}"