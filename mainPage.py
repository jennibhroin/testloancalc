import streamlit as st

st.set_page_config(page_title="Main Page", layout="centered")

st.title("Welcome to the Loan Application Portal")

st.header("Options")
st.markdown(
    """
    ### 1. [Loan Calculator](./testLoanCalc.py)
    - Click the link above to open the Loan Calculator.
    """
)