# Video-to-Tool Report
**Video:** WeChat_20260317004736.mp4  
**Frames:** 449  
**Generated:** 2026-03-17 16:48:06  
**Tokens used:** 14731

---

## Domain & Workflow

- **Domain**: HVAC (Heating, Ventilation, and Air Conditioning) design for cleanroom or pharmaceutical facilities.
- **Core workflow steps**:
  1. **Identify Room Requirements**: Gather specifications for each room, including air cleanliness level, air change rates, and target temperatures and humidity.
  2. **Calculate Air Volumes**: For each room, calculate the supply air volume, return air volume, fresh air volume, and exhaust air volume based on room specifications such as area and required air changes per hour (ACH).
  3. **Filter and Duct Selection**: Based on calculated air volumes, select appropriate filters (e.g., HEPA) and duct specifications to accommodate the calculated air flows.
  4. **Summarize System Requirements**: Aggregate the air volume calculations to define total HVAC system requirements for the entire facility.
  5. **Ensure Compliance**: Check against design conditions (temperature, humidity) and regulatory requirements for cleanrooms/pharmaceutical manufacturing (e.g., ASHRAE standards).

- **Key data fields**:
  - Room Number
  - Room Name
  - **Supply Air Volume** (m³/h)
  - **Return Air Volume** (m³/h)
  - **Fresh Air Volume** (m³/h)
  - **Exhaust Air Volume** (m³/h)
  - High-Efficiency Specification
  - Return Air Vent Specification and Quantity
  - Cleanliness Level
  - Air Change Rate (ACH)
  - Filter Specification and Quantity

- **Problem**: The video teaches how to calculate and balance air volumes for HVAC system design in a cleanroom environment, ensuring proper ventilation, filtration, and compliance with specific cleanliness and environmental control standards.

---

## Tool Design

- **Tool name and purpose**: `HVAC_Load_Calculator` - Automate the calculation of HVAC air volumes and summarize the system requirements for cleanroom facilities.

- **Data model**:
  - **Room**:
    - Room Number
    - Room Name
    - Area (m²)
    - Ceiling Height (m)
    - Cleanliness Level
    - Air Change Rate (ACH)
    - Supply Air Volume (m³/h)
    - Return Air Volume (m³/h)
    - Fresh Air Volume (m³/h)
    - Exhaust Air Volume (m³/h)
    - High-Efficiency Specification (e.g., filter type)
    - Return Air Vent Specification
    - Return Air Vent Quantity

- **Calculation logic**:
  - **Supply Air Volume** = Area * Ceiling Height * ACH
  - **Fresh Air Volume** = Based on external air intake requirements
  - **Return Air Volume** and **Exhaust Air Volume** based on balanced or direct exhaust systems

- **Validation rules**:
  - Supply Air Volume, Return Air Volume, Fresh Air Volume, Exhaust Air Volume cannot be negative.
  - Cleanliness Level must match predefined values (e.g., Class 100,000).
  - Air Change Rate must be reasonable for specified cleanliness (e.g., ≥15 ACH for cleanrooms).

- **Excel input schema**:
  - Room Number
  - Room Name
  - Area (m²)
  - Ceiling Height (m)
  - Cleanliness Level
  - Air Change Rate (ACH)
  - High-Efficiency Specification
  - Return Air Vent Specification
  - Return Air Vent Quantity

- **Excel output schema**:
  - Calculated Supply Air Volume (m³/h)
  - Calculated Return Air Volume (m³/h)
  - Calculated Fresh Air Volume (m³/h)
  - Calculated Exhaust Air Volume (m³/h)
  - Flag any calculation errors or validation issues

---

## Python Implementation

```python
```python
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
```

This Python script automates HVAC load calculations, validates results, and outputs both an Excel file and a summary report. Adjust the fraction of fresh and return air in `hvac_load_calculation` as per specific project needs.
```