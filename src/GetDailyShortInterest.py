from tkinter import Tk
from tkinter.filedialog import askopenfilename
import importlib
import numpy as np
import pandas as pd
from io import StringIO
from os import path
from pandas.tseries.offsets import BDay


Utils = importlib.import_module('utilities').Utils
TCM = importlib.import_module('TdaClientManager').TdaClientManager


class ShortInterestManager:

    # Reformatting short interest file to a proper csv
    @staticmethod
    def replace_line_to_comma(text):
        text = text.replace(',', '/')
        return text.replace('|', ',')

    # Writes to file, but ignores added newlines
    @staticmethod
    def write_data_to_file_no_newline(filename, data):
        with open(filename, 'a', newline='') as f:
            f.write(data)

    # From a selected file, load and reformat, then save to csv
    @staticmethod
    def load_short_interest_text_and_write_to_csv(filename):

        sel_file = filename.split('/')[-1]
        input_name = sel_file.split('.')[0]
        output = '../data/' + input_name + '.csv'

        f1 = open(filename, 'r')

        data = f1.read()
        data = ShortInterestManager.replace_line_to_comma(data)

        f1.close()

        ShortInterestManager.write_data_to_file_no_newline(output, data)

    # Uses util function to reformat latest trading day
    @staticmethod
    def get_latest_trading_day():
        return Utils.datetime_to_time_str(Utils.get_last_trading_day())

    @staticmethod
    def get_vix_close(tcm):

        vk = '$VIX.X'
        vq = tcm.get_quotes_from_tda([[vk]])

        return vq[vk]['closePrice']


    @staticmethod
    def get_past_short_vol(df, tickers, tcm, ymd, short_file_prefix):

        # Get data from past if exists
        files = Utils.find_file_pattern(short_file_prefix + '*', '../data/')
        prev_data_date = ymd
        if len(files) > 0:

            prev_data_file = files[-1]
            prev_data_file = prev_data_file.split('/')[-1]
            prev_data_date = prev_data_file[len(short_file_prefix):-4]

            if prev_data_date == ymd and len(files) > 1:
                prev_data_file = files[-2]
                prev_data_file = prev_data_file.split('/')[-1]
                prev_data_date = prev_data_file[len(short_file_prefix):-4]

        if prev_data_date != ymd:

            latest_data = '../data/' + short_file_prefix + prev_data_date + '.csv'
            latest_df = pd.read_csv(latest_data)

            try:
                latest_df = latest_df.set_index('Symbol')
            except:
                latest_df = latest_df

            # Convert to float
            cols = latest_df.columns[latest_df.dtypes.eq('object')]
            latest_df[cols] = latest_df[cols].apply(pd.to_numeric, errors='coerce').fillna(0)
            latest_df = latest_df.replace(np.nan, 0)

            prev_short_perc = latest_df['Short Interest Ratio']
            prev_vol_perc = latest_df['TotalVolume']
        else:

            th = tcm.get_past_history(tickers, Utils.time_str_to_datetime(ymd))
            prev_short_perc = df['TotalVolume']
            prev_vol_perc = []
            for key in th.keys():
                if len(th[key]) > 0:
                    prev_vol_perc.append(th[key][-1]['volume'])
                else:
                    prev_vol_perc.append(-1)

        return prev_short_perc, prev_vol_perc

    @staticmethod
    def cleanup_quotes_df(tcm, qs_df):

        # Clean up possible 0 values from TDA quotes
        bad_quote_tickers = qs_df[(qs_df['totalVolume'] == 0) | (qs_df['openPrice'] == 0) |
                                  (qs_df['regularMarketLastPrice'] == 0)].index.values

        if len(bad_quote_tickers) > 0:

            new_quotes = tcm.get_quotes_from_iex(bad_quote_tickers.tolist())

            # Just parse out possible bad entries
            new_vals = Utils.reduce_double_dict_to_one(new_quotes, ['latestVolume', 'open', 'close'])

            # If still have bad values, replace with -1
            for val in new_vals:
                for nt in val.keys():
                    if val[nt] is None:
                        val[nt] = -1

            qs_df['totalVolume'].update(pd.Series(new_vals[0]))
            qs_df['openPrice'].update(pd.Series(new_vals[1]))
            qs_df['regularMarketLastPrice'].update(pd.Series(new_vals[2]))

        # Add VIX close
        qs_df['VIX Close'] = ShortInterestManager.get_vix_close(tcm)

        return qs_df

    @staticmethod
    def generate_quotes_df(tcm, tickers_chunks):

        # Get some TDA data
        qs = tcm.get_quotes_from_tda(tickers_chunks)
        qs_df = pd.DataFrame(qs).transpose()  # Convert to dataframe

        return ShortInterestManager.cleanup_quotes_df(tcm, qs_df)

    @staticmethod
    def generate_fundamentals_df(tcm, tickers_chunks):

        fs = tcm.get_fundamentals_from_tda(tickers_chunks)
        fs_df = pd.DataFrame(fs).transpose()

        fs_df.replace(0, np.nan, inplace=True)

        return fs_df

    @staticmethod
    def generate_past_df(tcm, tickers, valid_dates):

        # Get one extra day to the start, for historical comparison
        valid_dates = [Utils.datetime_to_time_str(Utils.time_str_to_datetime(valid_dates[0]) - BDay(1))] + valid_dates

        # Add VIX to tickers list
        vk = '$VIX.X'
        tickers = tickers[:100]
        tickers = tickers + [vk]

        ps = tcm.get_past_history(tickers, Utils.time_str_to_datetime(valid_dates[0]),
                                  Utils.time_str_to_datetime(valid_dates[-1]))

        # Sort into a dictionary of historical data dataframes
        ps_dfs = {}
        for i in range(len(valid_dates)):

            temp = {}
            vc = -1
            for key, val in ps.items():
                temp[key] = val[i]

                if vc != -1 and key == vk:
                    vc = val[i]['close']

            ps_df = pd.DataFrame(temp).transpose()

            # Filter out tickers that don't meet volume and value criteria
            ps_df = ps_df[ps_df['volume'] > 1E6]
            ps_df = ps_df[ps_df['close'] > 5]

            ps_df.replace(0, np.nan, inplace=True)

            # Add VIX to dataframe
            ps_df['VIX Close'] = vc

            ps_dfs[valid_dates[i]] = ps_df

        return ps_dfs

    @staticmethod
    def regsho_txt_to_df(text, vol_lim=0):

        # Convert text into a dataframe
        sio = StringIO(text)
        df = pd.read_csv(sio, sep=',')[:-1]
        df = df[df['TotalVolume'] >= vol_lim]  # Only take rows with volumes greater than filter

        return df

    @staticmethod
    def update_short_df_with_data(df, qs, fs, prev_short_perc, prev_vol_perc):

        # Fill in some quote columns
        df['Exchange'] = fs['exchange']
        df['TotalVolume'] = qs['totalVolume']
        df['Open'] = qs['openPrice']
        df['Close'] = qs['regularMarketLastPrice']
        df['VIX Close'] = qs['VIX Close']
        df['Previous day\'s close change'] = qs['regularMarketPercentChangeInDouble'] / 100

        # Calculate short interest %
        short_int = df['ShortVolume'] / df['TotalVolume']
        df['Short Interest Ratio'] = short_int
        df['Previous short interest % change'] = short_int.sub(prev_short_perc).fillna(short_int)

        df['Previous volume delta'] = df['TotalVolume'].sub(prev_vol_perc).fillna(df['TotalVolume']).div(prev_vol_perc)

        # Calculate % close
        df['Open/close % change'] = (df['Close'] - df['Open']) / df['Open']

        # Add outstanding shares
        df['Total Volume/Shares Outstanding'] = df['TotalVolume'] / fs['sharesOutstanding']

        # Only take tickers whose volume delta isn't 0
        df = df.dropna()
        df = df.fillna(0)

        return df

    @staticmethod
    def update_short_df_with_past_data(df, ps, fs, date):

        # Sort dates and get old and new data
        dates = ps.keys()
        dates = dates.sort()

        old_date = dates[dates.index(date) - 1]
        old_data = ps[old_date]
        new_data = ps[date]

        return df

    @staticmethod
    def get_past_df(valid_dates, texts, short_file_prefix):

        tcm = TCM()
        ps_dfs = None
        fs = None
        dfs = {}
        for i in range(len(valid_dates)):

            text = texts[i]

            df = ShortInterestManager.regsho_txt_to_df(text)
            # Get list of tickers, separated into even chunks by TDA limiter
            tickers = df['Symbol'].tolist()
            tick_limit = 400  # TDA's limit for basic query
            tickers_chunks = [tickers[t:t + tick_limit] for t in range(0, len(tickers), tick_limit)]

            if ps_dfs is None:
                ps_dfs = ShortInterestManager.generate_past_df(tcm, tickers, valid_dates)

            # Set new index
            df = df.set_index('Symbol')

            # Get fundamental data
            if fs is None:
                fs = ShortInterestManager.generate_fundamentals_df(tcm, tickers_chunks)

            df = ShortInterestManager.update_short_df_with_past_data(df, ps_dfs, fs, valid_dates[i])

            dfs[valid_dates[i]] = df

        return dfs

    @staticmethod
    def get_today_df(ymd, text, short_file_prefix):

        df = ShortInterestManager.regsho_txt_to_df(text)

        # Get list of tickers, separated into even chunks by TDA limiter
        tickers = df['Symbol'].tolist()
        tick_limit = 400  # TDA's limit for basic query
        tickers_chunks = [tickers[t:t + tick_limit] for t in range(0, len(tickers), tick_limit)]

        # Set new index
        df = df.set_index('Symbol')

        tcm = TCM()
        qs_df = ShortInterestManager.generate_quotes_df(tcm, tickers_chunks)
        fs_df = ShortInterestManager.generate_fundamentals_df(tcm, tickers_chunks)

        # Drop symbols with missing data by joining on matching symbols
        qs_syms = qs_df.index.tolist()
        fs_syms = fs_df.index.tolist()

        # Because getting quotes is inconsistent, find missing tickers between quotes and funds and re-get quotes
        excess_tickers = []
        for fs in fs_syms:
            if fs not in qs_syms:
                excess_tickers.append(fs)

        excess_quotes = tcm.get_quotes_from_tda([excess_tickers])
        eq_df = pd.DataFrame(excess_quotes).transpose()  # Convert to dataframe
        eq_df = ShortInterestManager.cleanup_quotes_df(tcm, eq_df)

        # Append cleaned new quotes
        qs_df.append(eq_df)
        qs_df = qs_df.sort_index()

        # Filter on volume minimum and open price minimum
        qs_df = qs_df[qs_df['totalVolume'] > 1E6]
        qs_df = qs_df[qs_df['regularMarketLastPrice'] > 5]

        # Remove any remaining excess tickers
        qs_syms = qs_df.index.tolist()
        fs_df = fs_df.loc[qs_syms]
        df = df.loc[qs_syms]

        (prev_short_perc, prev_vol_perc) = ShortInterestManager.get_past_short_vol(df, qs_syms, tcm, ymd,
                                                                                   short_file_prefix)

        df = ShortInterestManager.update_short_df_with_data(df, qs_df, fs_df, prev_short_perc, prev_vol_perc)

        return df

    # Gets file from regsho consolidated short interest using a YYYYMMDD format and write to csv
    @staticmethod
    def get_regsho_daily_short_to_csv(ymd, ymd2=''):

        url = 'http://regsho.finra.org'
        short_file_prefix = 'CNMSshvol'

        outputs = []
        valid_dates = []
        texts = []
        date_range = Utils.get_bd_range(ymd, ymd2)

        for date in date_range:

            filename = short_file_prefix + date
            out = '../data/' + filename + '.csv'

            # Check if date already saved
            if path.exists(out):
                continue

            data = Utils.get_file_from_url(url, filename + '.txt')
            text = ShortInterestManager.replace_line_to_comma(data)

            # If date not found, find next most recent date
            if '404 Not Found' in text:

                # Get most recent past trading day
                past_td = Utils.datetime_to_time_str(
                    Utils.get_recent_trading_day_from_date(Utils.time_str_to_datetime(date)))

                filename = short_file_prefix + past_td
                out = '../data/' + filename + '.csv'

                data = Utils.get_file_from_url(url, filename + '.txt')
                text = ShortInterestManager.replace_line_to_comma(data)

            valid_dates.append(date)
            texts.append(text)
            outputs.append(out)

        if not outputs:
            return ['']

        # Check if date passed is current day. If not, cannot use quotes
        if Utils.is_it_today(ymd):
            df = ShortInterestManager.get_today_df(ymd, texts[0], short_file_prefix)
            Utils.write_dataframe_to_csv(df, outputs[0])
        else:
            dfs = ShortInterestManager.get_past_df(valid_dates, texts, short_file_prefix)

            for i in range(len(outputs)):
                Utils.write_dataframe_to_csv(dfs[i], outputs[i])

            return outputs

        return outputs

    # Call function to write latest trading day's short interest to a csv
    @staticmethod
    def get_latest_short_interest_data():
        ltd = ShortInterestManager.get_latest_trading_day()
        return ShortInterestManager.get_regsho_daily_short_to_csv(ltd)

    @staticmethod
    def import_short_interest_text_from_selection():

        Tk().withdraw()
        f_name = askopenfilename()

        ShortInterestManager.load_short_interest_text_and_write_to_csv(f_name)


def main():

    sim = ShortInterestManager
    res = sim.get_latest_short_interest_data()
    #res = sim.get_regsho_daily_short_to_csv('20201116')
    #Utils.upload_file_to_gdrive(res, 'Daily Short Data')


if __name__ == '__main__':
    main()
