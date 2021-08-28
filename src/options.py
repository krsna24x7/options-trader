import json
import pandas as pd
from typing import List
from copy import deepcopy
from datetime import datetime, timedelta
from multiprocessing import Pool

import requests

try:
    from ._variables import VARIABLES
    from . import utilities as Utilities
    from . import models
    from .instruments import get_instrument_details
except:
    # In order to run the module in isolation, following is required
    # This enables local testing
    from _variables import VARIABLES
    import utilities as Utilities


def get_time_to_expiry_in_days(option: dict):
    expiry = datetime.strptime(option['expiry'], VARIABLES.DATETIME_FORMAT)
    today = datetime.now()

    return (expiry - today).days


def get_full_option_chain(instrument_id: str):
    response = requests.get(
        'https://api.sensibull.com/v1/instruments/%s' % instrument_id
    )

    if response.status_code != 200:
        raise Exception('Invalid response code found: %s, expected: 200' % response.status_code)

    return response.json()


def get_option_margin_bulk(options: dict):
    # Note: Hard-coded for options and SELL type for now
    # TODO: Possibly change to leverage BUY as well
    data = []

    for option in options:
        data.append({
            'exchange': 'NFO',
            'tradingsymbol': option['tradingsymbol'],
            'transaction_type': 'SELL',
            'product': 'NRML',
            'variety': 'regular',
            'order_type': 'limit',
            'quantity': int(option['lot_size']) if option['lot_size'].is_integer() else option['lot_size'],
            'price': option['last_price']
        })

    response = requests.post(
        'https://kite.zerodha.com/oms/margins/orders',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f"enctoken {VARIABLES.CONFIG['auth_token']}"
        },
        data=json.dumps(data)
    )

    if response.status_code != 200:
        raise Exception('Invalid response code found: %s, expected: 200' % response.status_code)

    return response.json()['data']


def get_instrument_options_of_interest(options: list, instrument: dict):
    instrument_price = instrument['last_price']
    selected_options = []

    for option in options:
        if option['instrument_type'] != 'PE':  # only interested in PE options right now
            continue

        if option['strike'] > instrument_price:  # only interested in valid PE i.e. less price than instrument_price
            continue

        if get_time_to_expiry_in_days(option=option) > VARIABLES.MAX_TIME_TO_EXPIRY:  # only looking for options within next X numeber of days
            continue

        percentage_dip = (instrument_price - option['strike']) / instrument_price * 100

        if VARIABLES.PE_OPTIONS_OF_INTEREST_THRESHOLD['min'] < percentage_dip < VARIABLES.PE_OPTIONS_OF_INTEREST_THRESHOLD['max']:
            option['percentage_dip'] = percentage_dip

            selected_options.append(option)

    return selected_options


def add_margins(options: list):
    margin_data = get_option_margin_bulk(options=options)

    for iteration, option in enumerate(options):
        option['margin_data'] = margin_data[iteration]

    return options


def add_profits(options: list):
    for option in options:
        try:
            base = option['margin_data']['total']
            profit = option['last_price'] * option['lot_size']

            option['profit_data'] = {
                'value': profit,
                'percentage': profit / base * 100
            }
        except Exception as ex:
            print(ex)
            print(option)
            raise ex

    return options


def add_instrument(instrument: dict, options: list):
    for option in options:
        option['instrument_data'] = instrument

    return options


def get_margin():
    response = requests.get(
        'https://kite.zerodha.com/oms/user/margins',
        headers={
            'Authorization': f"enctoken {VARIABLES.CONFIG['auth_token']}",
            'Content-Type': 'application/json'
        }
    )

    if response.status_code != 200:
        raise Exception('Invalid response code found: %s, expected: 200' % response.status_code)

    return response.json()['data']['equity']['net']


def sort_options(options: list):
    return sorted(options, key=lambda x: x['profit_data']['percentage'] + x['percentage_dip'], reverse=True)


def filter_options(options: list):
    filtered_options = []

    for option in options:
        if option['profit_data']['percentage'] <= VARIABLES.MINIMUM_PROFIT_PERCENTAGE:
            continue

        filtered_options.append(option)

    return filtered_options


def get_options_of_interest(stocks: List[models.StockOfInterest]):
    all_options = []

    for stock in stocks:
        stock = models.StockOfInterest(**stock)
        instrument = get_instrument_details(instrument_id=stock.ticker)
        option_chain = get_full_option_chain(instrument_id=stock.ticker)

        options_of_interest = get_instrument_options_of_interest(options=option_chain['data'], instrument=instrument)

        options_of_interest = add_margins(options=options_of_interest)
        options_of_interest = add_profits(options=options_of_interest)
        options_of_interest = add_instrument(instrument=instrument, options=options_of_interest)

        all_options += options_of_interest

    options = sort_options(options=all_options)
    options = filter_options(options=options)

    for seq_no in range(len(options)):
        options[seq_no]['sequence_id'] = seq_no + 1

    return options


def get_options_of_interest_df(stocks: list) -> models.ProcessedOptions:
    options_of_interest = get_options_of_interest(stocks=stocks)

    flattened_options_of_interest = Utilities.dict_array_to_df(dict_array=options_of_interest)

    df = pd.DataFrame(flattened_options_of_interest)

    if len(df) == 0:
        return df

    df['backup_money'] = df['lot_size'] * df['strike']

    return df


def get_option_last_price(tradingsymbol: str, underlying_instrument: str) -> float:
    option_chain = get_full_option_chain(instrument_id=underlying_instrument)

    for option in option_chain['data']:
        if option['tradingsymbol'] == tradingsymbol:
            return option['last_price']

    return None


def select_options(options: list, selection: str):
    option_dict = dict((option['seq'], option) for option in options)
    selected_options = []
    expected_info = {
        'profit': 0,
        'margin': 0
    }

    for selected in selection.split(','):
        option = option_dict[int(selected)]

        selected_options.append(option)

        expected_info['profit'] += option['profit']
        expected_info['margin'] += option['margin']

    print('Expected profit: %d, margin: %d' % (expected_info['profit'], expected_info['margin']))

    return selected_options
