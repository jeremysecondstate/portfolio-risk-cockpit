from dataclasses import dataclass
from typing import Dict

@dataclass
class CapitalStructure:
    shares_outstanding: float
    public_float: float
    free_float: float
    insider_ownership: float
    institutional_ownership: float
    share_class_breakdown: Dict[str, float]
    ads_ratio: float
    recent_share_count_changes: float

def calculate_capital_structure_pressure(capital_structure: CapitalStructure) -> float:
    # Example calculation, actual implementation depends on the specific requirements
    pressure = (capital_structure.shares_outstanding * 0.3 + 
                capital_structure.public_float * 0.2 + 
                capital_structure.free_float * 0.2 + 
                capital_structure.insider_ownership * 0.1 + 
                capital_structure.institutional_ownership * 0.1 + 
                sum(capital_structure.share_class_breakdown.values()) * 0.05 + 
                capital_structure.ads_ratio * 0.05)
    return pressure

def assess_capital_structure(capital_structure: CapitalStructure) -> str:
    pressure = calculate_capital_structure_pressure(capital_structure)
    if pressure > 0.7:
        return "High Pressure"
    elif pressure > 0.4:
        return "Moderate Pressure"
    else:
        return "Low Pressure"

# Example usage
capital_structure = CapitalStructure(
    shares_outstanding=1000000,
    public_float=0.6,
    free_float=0.5,
    insider_ownership=0.2,
    institutional_ownership=0.3,
    share_class_breakdown={"Class A": 0.7, "Class B": 0.3},
    ads_ratio=1.2,
    recent_share_count_changes=0.05
)

print(assess_capital_structure(capital_structure))