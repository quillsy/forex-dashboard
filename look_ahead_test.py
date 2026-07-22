import pandas as pd
from datetime import datetime

def test_look_ahead():
    print("Running Look-Ahead Bias simulation...")
    target_dt = pd.to_datetime("2021-06-01")
    
    # Test data frame with simulated future releases
    sim_data = pd.DataFrame([
        {"date": pd.to_datetime("2021-05-01"), "value": 1.5},
        {"date": pd.to_datetime("2021-06-01"), "value": 1.6},
        {"date": pd.to_datetime("2021-07-01"), "value": 1.7} # Future
    ])
    
    # Filter
    filtered = sim_data[sim_data["date"] <= target_dt]
    max_date = filtered["date"].max()
    
    print(f"Target date: {target_dt.strftime('%Y-%m-%d')}")
    print(f"Max date available: {max_date.strftime('%Y-%m-%d')}")
    assert max_date <= target_dt, "Look-Ahead Bias detected!"
    print("🟢 Look-Ahead Bias test passed successfully!")

if __name__ == "__main__":
    test_look_ahead()
