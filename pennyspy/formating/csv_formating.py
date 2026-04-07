from datetime import date
from logging import getLogger
from pathlib import Path

import pandas as pd
from pandas import DataFrame

logger = getLogger(__name__)


def trim_csv(input_file, output_file, start_date: date, end_date: date, date_col: str = "Date"):
    """
    Filters a CSV file based on a date column.

    :param date_col: The name of the column containing dates (e.g., 'Date' or 'Posted Date')
    :param start_date: datetime object for the start of the range
    :param end_date: datetime object for the end of the range
    """
    df = pd.read_csv(input_file)

    df[date_col] = pd.to_datetime(df[date_col])

    mask = (df[date_col] >= pd.Timestamp(start_date)) & (df[date_col] <= pd.Timestamp(end_date))

    filtered_df = df.loc[mask].copy()

    filtered_df[date_col] = filtered_df[date_col].dt.strftime("%Y-%m-%d")

    filtered_df.to_csv(output_file, index=False)

    logger.info(f"Processed {len(df)} rows. Kept {len(filtered_df)} rows.")


def get_column_possible_values(csv_path: str | Path, column_name: str) -> set[str]:
    try:
        df = pd.read_csv(csv_path)
        if column_name in df.columns:
            unique_values = set(df[column_name].unique())
            return unique_values
        else:
            raise ValueError(f"Error: Column '{column_name}' not found in {csv_path}.")
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {csv_path}")
    except Exception as e:
        raise RuntimeError(f"An unexpected error occurred: {e}") from e


def filter_csv_by_value(csv_path, column_name: str, target_value) -> DataFrame:
    df = pd.read_csv(csv_path)

    if column_name not in df.columns:
        raise ValueError(f"Error: Column '{column_name}' not found in file {csv_path}.")

    filtered_df: DataFrame = df[df[column_name] == target_value]

    return filtered_df
