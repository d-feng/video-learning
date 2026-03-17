import pandas as pd
import openpyxl

def hvac_load_calculation(df):
    """Perform HVAC load calculations."""
    df['Supply Air Volume (m³/h)'] = df['Area (m²)'] * df['Ceiling Height (m)'] * df['Air Change Rate (ACH)']
    df['Return Air Volume (m³/h)'] = df['Supply Air Volume (m³/h)'] * 0.8  # Assume 80% return
    df['Fresh Air Volume (m³/h)'] = df['Supply Air Volume (m³/h)'] * 0.2  # Assume 20% fresh
    df['Exhaust Air Volume (m³/h)'] = df['Fresh Air Volume (m³/h)']

    # Validate and flag errors
    df['Validation'] = 'Valid'
    conditions = [
        (df['Supply Air Volume (m³/h)'] < 0),
        (df['Return Air Volume (m³/h)'] < 0),
        (df['Fresh Air Volume (m³/h)'] < 0),
        (df['Exhaust Air Volume (m³/h)'] < 0),
        (df['Cleanliness Level'].isnull() | ~df['Cleanliness Level'].isin(['Class 100,000', 'None']))
    ]
    df['Validation'] = df['Validation'].where(~(conditions[0] | conditions[1] | conditions[2] | conditions[3] | conditions[4]), 'Error')
    return df

def demo_data():
    """Generate demo HVAC data."""
    data = {
        "Room Number": [101, 102, 103, 104],
        "Room Name": ["Preparation", "Mixing", "Filling", "Packaging"],
        "Area (m²)": [50, 75, 55, 60],
        "Ceiling Height (m)": [2.5, 2.5, 2.5, 2.5],
        "Cleanliness Level": ['Class 100,000', 'Class 100,000', 'Class 100,000', 'Class 100,000'],
        "Air Change Rate (ACH)": [15, 15, 20, 20],
        "High-Efficiency Specification": ["H13", "H13", "H14", "H14"],
        "Return Air Vent Specification": ["H1", "H2", "H1", "H3"],
        "Return Air Vent Quantity": [2, 3, 1, 2]
    }
    return pd.DataFrame(data)

def run(input_excel=None, output_excel=None):
    """Main function to load data, process it, and save results."""
    if input_excel:
        df = pd.read_excel(input_excel)
    else:
        df = demo_data()

    df = hvac_load_calculation(df)

    # Save to Excel and plain-text summary
    if output_excel:
        df.to_excel(output_excel, index=False)

    with open('summary.txt', 'w') as f:
        f.write(df.to_string())

if __name__ == "__main__":
    import sys
    input_excel = sys.argv[1] if len(sys.argv) > 1 else None
    output_excel = sys.argv[2] if len(sys.argv) > 2 else "output.xlsx"
    run(input_excel, output_excel)