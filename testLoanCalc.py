import streamlit as st
import pandas as pd
from datetime import timedelta
import json

# --- Backend Calculation Logic (Copied from previous immersive for self-containment) ---
def day_count_fraction(start_date, end_date, convention):
    """
    Calculates the day count fraction between two dates based on the specified convention.
    This function is primarily for calculating the fraction for a period, though for
    daily accruals, we use a simplified 1/DaysInYear basis.
    """
    # Ensure dates are pandas Timestamps for consistent operations
    if not isinstance(start_date, pd.Timestamp):
        start_date = pd.to_datetime(start_date)
    if not isinstance(end_date, pd.Timestamp):
        end_date = pd.to_datetime(end_date)

    if convention == 'Actual/365':
        # Actual number of days / 365
        return (end_date - start_date).days / 365.0
    elif convention == '30/360':
        # 30/360 day count convention (ISDA or Bond Basis commonly)
        d1 = start_date.day
        m1 = start_date.month
        y1 = start_date.year
        d2 = end_date.day
        m2 = end_date.month
        y2 = end_date.year

        # Adjust day 31 to 30 for start date
        if d1 == 31:
            d1 = 30
        # Adjust day 31 to 30 for end date if start day is 30 or 31
        if d2 == 31 and (d1 == 30 or d1 == 31):
            d2 = 30

        # Calculate fraction based on 30/360 formula
        return (360 * (y2 - y1) + 30 * (m2 - m1) + (d2 - d1)) / 360.0
    else:
        # Default to Actual/365 if an unsupported convention is provided
        st.warning(f"Warning: Unsupported day count convention '{convention}'. Defaulting to 'Actual/365'.")
        return (end_date - start_date).days / 365.0

def calculate_loan_accruals(loan_data_json, market_rates_json, calculation_date_str):
    """
    Calculates daily and cumulative interest accruals for a loan portfolio.

    Args:
        loan_data_json (str): JSON string representing the loan portfolio data.
                              Expected to be an array of loan objects.
        market_rates_json (str): JSON string representing market rates.
                                 Expected to be an object with 'sonia_rates' (daily rates)
                                 and 't_bill_rates' (e.g., '1-Month T-Bill' rate).
        calculation_date_str (str): The specific date for which to calculate the daily
                                    accrual and the end date for cumulative accrual (YYYY-MM-DD).

    Returns:
        pd.DataFrame: The updated DataFrame with all interest accruals, or an empty DataFrame
                      if an error occurs or no loans match the criteria.
    """
    try:
        # 1. Load Data
        loan_df = pd.read_json(loan_data_json)
        market_rates = json.loads(market_rates_json)
        calculation_date = pd.to_datetime(calculation_date_str)

        # Convert loan date columns to datetime objects for proper comparison and calculation
        loan_df['Start_Date'] = pd.to_datetime(loan_df['Start_Date'])
        loan_df['End_Date'] = pd.to_datetime(loan_df['End_Date'])

        # 2. Filter Loans
        # Filter for loans that are marked as 'Live_Mask' true and whose active period
        # includes or extends beyond the calculation date.
        filtered_df = loan_df[
            (loan_df['Live_Mask'] == True) &
            (loan_df['Start_Date'] <= calculation_date) &
            (loan_df['End_Date'] >= calculation_date)
        ].copy() # Use .copy() to avoid SettingWithCopyWarning

        # If no loans match the criteria, return an empty DataFrame
        if filtered_df.empty:
            return pd.DataFrame()

        # Prepare SONIA rates DataFrame for efficient lookup using 'asof'
        sonia_rates_data = market_rates.get('sonia_rates', {})
        sonia_rates_df = pd.DataFrame.from_dict(sonia_rates_data, orient='index', columns=['Rate'])
        if not sonia_rates_df.empty:
            sonia_rates_df.index = pd.to_datetime(sonia_rates_df.index)
            sonia_rates_df = sonia_rates_df.sort_index() # Ensure index is sorted for asof

        # Get the 1-Month T-bill rate
        t_bill_rate = market_rates.get('t_bill_rates', {}).get('1-Month T-Bill', 0.0)

        # List to store results for each loan
        results_list = []

        # 3. Calculate Accruals for Each Loan
        for index, loan in filtered_df.iterrows():
            loan_id = loan['Loan_ID']
            principal = loan['Principal']
            loan_start_date = loan['Start_Date']
            loan_end_date = loan['End_Date']
            interest_rate_type = loan['Interest_Rate_Type']
            fixed_rate = loan['Fixed_Rate']
            margin = loan['Margin']
            benchmark = loan['Benchmark']
            day_count_convention = loan['Day_Count_Convention']

            daily_accrued_interest_on_calc_date = 0.0
            cumulative_accrued_interest_for_loan = 0.0

            # Determine the period for which to calculate cumulative interest.
            # This runs from the loan's start date up to the calculation date (inclusive).
            accrual_period_start = loan_start_date
            accrual_period_end = calculation_date

            # Iterate day by day within the accrual period to calculate daily interest
            current_day_in_period = accrual_period_start
            while current_day_in_period <= accrual_period_end:
                effective_benchmark_rate = 0.0

                # Determine the benchmark rate based on the loan type
                if interest_rate_type == 'Fixed':
                    effective_benchmark_rate = fixed_rate
                elif interest_rate_type == 'Floating':
                    if benchmark == 'SONIA':
                        # For SONIA, retrieve the rate for the specific day.
                        # Use .asof() to get the last valid observation on or before the current day.
                        sonia_rate_for_day = 0.0
                        if not sonia_rates_df.empty:
                            rate_series = sonia_rates_df['Rate'].asof(current_day_in_period)
                            if pd.notna(rate_series): # Check if a rate was found
                                sonia_rate_for_day = rate_series
                        effective_benchmark_rate = sonia_rate_for_day
                    elif benchmark == '1-Month T-Bill':
                        effective_benchmark_rate = t_bill_rate
                    else:
                        # Handle cases where benchmark is floating but not supported/found
                        effective_benchmark_rate = 0.0 # Default to 0, or raise specific error
                
                # Total annual rate includes the benchmark rate and the loan's margin
                total_annual_rate = effective_benchmark_rate + margin

                # Determine the days in year basis for the daily fraction
                days_in_year_basis = 365.0 # Default for Actual/365 and SONIA
                if day_count_convention == '30/360':
                    days_in_year_basis = 360.0
                
                # Calculate the daily interest for the current day
                # Daily interest = Principal * Total Annual Rate * (1 / Days in Year Basis)
                daily_interest_for_current_day = principal * total_annual_rate * (1.0 / days_in_year_basis)

                # Accumulate daily interest for the cumulative total
                cumulative_accrued_interest_for_loan += daily_interest_for_current_day

                # If the current day in the loop is the calculation date, store this as the daily accrual
                if current_day_in_period == calculation_date:
                    daily_accrued_interest_on_calc_date = daily_interest_for_current_day

                # Move to the next day
                current_day_in_period += timedelta(days=1)

            # Append the calculated results for the current loan to the list
            results_list.append({
                'Loan_ID': loan_id,
                'Principal': principal,
                'Start_Date': loan_start_date.strftime('%Y-%m-%d'),
                'End_Date': loan_end_date.strftime('%Y-%m-%d'),
                'Interest_Rate_Type': interest_rate_type,
                'Fixed_Rate': fixed_rate,
                'Margin': margin,
                'Benchmark': benchmark,
                'Day_Count_Convention': day_count_convention,
                'Live_Mask': loan['Live_Mask'],
                'Calculation_Date': calculation_date.strftime('%Y-%m-%d'),
                'Daily_Accrued_Interest_on_Calc_Date': daily_accrued_interest_on_calc_date,
                'Cumulative_Accrued_Interest_to_Calc_Date': cumulative_accrued_interest_for_loan
            })

        # Convert the list of results into a Pandas DataFrame
        output_df = pd.DataFrame(results_list)
        return output_df

    except Exception as e:
        st.error(f"Error during calculation: {e}")
        return pd.DataFrame()

# --- Streamlit Frontend ---

st.set_page_config(layout="wide", page_title="Loan Accrual Calculator")

st.title("💰 Loan Accrual Calculator")
st.markdown("""
    This tool calculates daily and cumulative interest accruals for a loan portfolio.
    Input your loan data and market rates in JSON format, specify a calculation date,
    and see the accrued interest for each live loan.
""")

# Sample data for user reference
sample_loan_data = json.dumps([
    {
        "Loan_ID": "L001",
        "Principal": 1000000,
        "Start_Date": "2024-01-01",
        "End_Date": "2025-01-01",
        "Interest_Rate_Type": "Fixed",
        "Fixed_Rate": 0.05,
        "Margin": 0.01,
        "Benchmark": None,
        "Day_Count_Convention": "Actual/365",
        "Live_Mask": True
    },
    {
        "Loan_ID": "L002",
        "Principal": 500000,
        "Start_Date": "2024-03-15",
        "End_Date": "2025-03-15",
        "Interest_Rate_Type": "Floating",
        "Fixed_Rate": None,
        "Margin": 0.005,
        "Benchmark": "SONIA",
        "Day_Count_Convention": "Actual/365",
        "Live_Mask": True
    },
    {
        "Loan_ID": "L003",
        "Principal": 200000,
        "Start_Date": "2024-06-01",
        "End_Date": "2024-12-01",
        "Interest_Rate_Type": "Floating",
        "Fixed_Rate": None,
        "Margin": 0.002,
        "Benchmark": "1-Month T-Bill",
        "Day_Count_Convention": "30/360",
        "Live_Mask": False
    }
], indent=2)

sample_market_rates = json.dumps({
    "sonia_rates": {
        "2024-01-01": 0.04,
        "2024-01-02": 0.0401,
        "2024-01-03": 0.0402,
        "2024-03-15": 0.041,
        "2024-03-16": 0.0411,
        "2024-03-17": 0.0412,
        "2024-06-01": 0.042,
        "2024-06-02": 0.0421,
        "2024-06-15": 0.0425 # Added for calculation date
    },
    "t_bill_rates": {
        "1-Month T-Bill": 0.035
    }
}, indent=2)

# Input Section
st.header("Input Data")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Loan Portfolio Data")
    loan_data_input = st.text_area(
        "Enter your loan data as a JSON array:",
        value=sample_loan_data,
        height=300,
        help="Each object in the array represents a loan. See example for structure."
    )

with col2:
    st.subheader("Market Rates Data")
    market_rates_input = st.text_area(
        "Enter your market rates as a JSON object:",
        value=sample_market_rates,
        height=300,
        help="Include 'sonia_rates' (daily) and 't_bill_rates' (fixed for now)."
    )

st.subheader("Calculation Date")
calculation_date = st.date_input(
    "Select the calculation date:",
    value=pd.to_datetime("2024-06-15"), # Default date
    help="Interest will be accrued up to and including this date for live loans."
)

if st.button("Calculate Accruals"):
    if not loan_data_input or not market_rates_input or not calculation_date:
        st.error("Please provide all required inputs: Loan Data, Market Rates, and Calculation Date.")
    else:
        try:
            # Call the calculation function
            with st.spinner('Calculating interest accruals...'):
                results_df = calculate_loan_accruals(
                    loan_data_input,
                    market_rates_input,
                    calculation_date.strftime('%Y-%m-%d')
                )

            if not results_df.empty:
                st.header("Calculation Results")
                # Display the DataFrame
                st.dataframe(results_df.style.format({
                    'Principal': "{:,.2f}",
                    'Fixed_Rate': "{:.4%}",
                    'Margin': "{:.4%}",
                    'Daily_Accrued_Interest_on_Calc_Date': "{:,.4f}",
                    'Cumulative_Accrued_Interest_to_Calc_Date': "{:,.4f}"
                }), use_container_width=True)
            else:
                st.info("No live loans found for the specified calculation date, or an error occurred during calculation. Please check your inputs and the 'Live_Mask' for loans.")

        except json.JSONDecodeError:
            st.error("Invalid JSON format in Loan Data or Market Rates. Please check your input.")
        except Exception as e:
            st.error(f"An unexpected error occurred: {e}")

