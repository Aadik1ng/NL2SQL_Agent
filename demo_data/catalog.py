"""The raw ingredients of the demo data: places, company and people names, products, reps, codes and seasonality."""

CITIES = [("Mumbai", "Maharashtra", 27), ("Pune", "Maharashtra", 27), ("Nagpur", "Maharashtra", 27),
          ("Delhi", "Delhi", 7), ("Bengaluru", "Karnataka", 29), ("Chennai", "Tamil Nadu", 33),
          ("Coimbatore", "Tamil Nadu", 33), ("Hyderabad", "Telangana", 36), ("Ahmedabad", "Gujarat", 24),
          ("Surat", "Gujarat", 24), ("Rajkot", "Gujarat", 24), ("Kolkata", "West Bengal", 19),
          ("Jaipur", "Rajasthan", 8), ("Ludhiana", "Punjab", 3), ("Indore", "Madhya Pradesh", 23),
          ("Kochi", "Kerala", 32), ("Lucknow", "Uttar Pradesh", 9)]
NAME_HEADS = ["Sharma", "Gupta", "Agarwal", "Patel", "Mehta", "Shah", "Reddy", "Rao", "Iyer", "Nair", "Kulkarni",
              "Joshi", "Desai", "Malhotra", "Kapoor", "Bansal", "Jain", "Chopra", "Shree Ganesh", "Balaji",
              "Sai Krishna", "Lakshmi", "Om Sai", "Jai Bharat", "Navkar", "Siddhi Vinayak", "Mahalaxmi",
              "Vardhman", "Sunrise", "Apex", "Pioneer", "Trident", "Galaxy", "Supreme", "Royal", "Excel",
              "Orient", "Everest", "Unity", "Vikas", "Kaveri", "Ganga", "Narmada", "Deccan", "Konark", "Ashoka"]
NAME_TAILS = ["Traders", "Enterprises", "Industries", "Distributors", "Agencies", "Udyog", "Engineering",
              "Polymers", "Steel", "Hardware", "Electricals", "Pharma", "Textiles", "Foods", "Auto Parts",
              "Packaging", "Chemicals", "Infra", "Exports", "Machine Tools"]
LEGAL = ["", "", "", " Pvt Ltd", " Pvt Ltd", " LLP", " & Co"]
CATALOG = {  # category: (sku prefix, min price, max price, product names)
    "Bearings": ("BRG", 180, 4500, ["Ball Bearing 6204", "Ball Bearing 6305", "Taper Roller Bearing 32210",
                                    "Pillow Block UCP208", "Needle Roller Bearing NK25", "Spherical Bearing 22216"]),
    "Valves": ("VLV", 1200, 38000, ["Gate Valve 2in CI", "Ball Valve 1in SS304", "Butterfly Valve 4in",
                                    "Non-Return Valve 3in", "Solenoid Valve 24V DC"]),
    "Pipes & Fittings": ("PIP", 250, 9000, ["GI Pipe 1in 6m", "MS ERW Pipe 2in", "SS Elbow 1.5in",
                                            "HDPE Pipe 63mm 6m", "Pipe Flange 4in"]),
    "Cables": ("CBL", 900, 65000, ["Armoured Cable 4C 16sqmm 100m", "Control Cable 12C 100m",
                                   "FR House Wire 2.5sqmm 90m", "Flexible Cable 3C 1.5sqmm 100m"]),
    "Motors": ("MTR", 14000, 240000, ["Induction Motor 3HP", "Induction Motor 10HP", "Geared Motor 1HP",
                                      "Servo Motor 2kW", "Flameproof Motor 7.5HP"]),
    "Pumps": ("PMP", 6500, 120000, ["Centrifugal Pump 2HP", "Submersible Pump 5HP", "Dosing Pump 10LPH",
                                    "Gear Pump 15LPM"]),
    "Fasteners": ("FST", 150, 2500, ["Hex Bolt M12 box-100", "SS Nut M10 box-200", "Anchor Fastener M16 box-50",
                                     "Spring Washer M8 box-500", "Allen Bolt M6 box-100"]),
    "Safety Gear": ("PPE", 200, 6000, ["Safety Helmet ISI", "Safety Shoes S3", "Nitrile Gloves box-100",
                                       "Full Body Harness", "Safety Goggles"]),
    "Lubricants": ("LUB", 450, 18000, ["Hydraulic Oil 68 20L", "Gear Oil EP90 20L", "Lithium Grease 18kg",
                                       "Cutting Oil 20L"]),
    "Packaging": ("PKG", 300, 8000, ["Stretch Film 23mic", "Corrugated Box 5-ply x100", "BOPP Tape 72mm x72",
                                     "Pallet Strapping Roll"]),
    "Electrical Panels": ("ELC", 2500, 150000, ["MCB 32A TP", "MCCB 250A", "VFD 7.5kW", "Contactor 40A",
                                                "PLC Starter Kit"]),
    "Power Tools": ("TLS", 2200, 45000, ["Angle Grinder 4in", "Rotary Hammer 26mm", "Cordless Drill 18V",
                                         "Bench Grinder 8in"]),
}
VENDOR_HEADS = ["Precision", "Bharat", "National", "Allied", "Standard", "Shakti", "Kalyan", "Venkatesh", "Mahavir",
                "Ambica", "Tirupati", "Durga", "Neelkanth", "Satyam", "Ajanta", "Hari Om", "Classic", "Elite",
                "Modern", "Pragati", "Sapphire", "Western", "Eastern", "Southern"]
VENDOR_TAILS = [" Supplies", " Mfg Co", " Industries", " Corporation", " Sales"]
REPS = ["Priya Nair", "Rahul Verma", "Ankit Sharma", "Sneha Kulkarni", "Vikram Rao", "Meera Iyer", "Arjun Mehta",
        "Kavya Reddy"]
FIRST = ["Rajesh", "Suresh", "Anita", "Pooja", "Amit", "Neha", "Vijay", "Deepa", "Sanjay", "Kiran", "Manoj", "Ravi",
         "Sunita", "Arun", "Divya", "Prakash", "Swati", "Naveen", "Asha", "Imran", "Farah", "Joseph", "Mary",
         "Gurpreet", "Harpreet"]
LAST = ["Kumar", "Sharma", "Patel", "Reddy", "Nair", "Iyer", "Shah", "Gupta", "Joshi", "Khan", "D'Souza", "Singh",
        "Das", "Bose", "Pillai", "Verma"]
TITLES = ["Purchase Manager", "Owner", "Accounts Head", "Plant Head", "CFO", "Procurement Lead", "Director"]
REASONS = ["DISPUTE_PRICING", "DISPUTE_QUALITY", "PO_MISMATCH", "CASH_FLOW", "AWAITING_APPROVAL", "DELIVERY_SHORTFALL"]
SUBJECTS = {"call": ["Payment follow-up", "Order status", "Quarterly check-in", "Price negotiation"],
            "email": ["Quote sent", "Invoice reminder", "New product catalog", "Follow-up on proposal"],
            "meeting": ["Site visit", "Annual review", "Product demo", "Escalation meeting"]}
NPS_COMMENTS = {"promoter": ["Reliable delivery, good support", "Competitive pricing, responsive team",
                             "Quick turnaround on urgent orders"],
                "passive": ["Okay overall, delivery sometimes delayed", "Products fine, occasional invoicing errors",
                            "Average service"],
                "detractor": ["Repeated quality issues", "Billing disputes not resolved",
                              "Delivery delays hurting our production", "Sales team unresponsive"]}
# Diwali (Oct-Nov) and fiscal year-end (March) peaks, monsoon dip
SEASON = {1: 1.0, 2: 1.0, 3: 1.5, 4: 0.9, 5: 0.85, 6: 0.75, 7: 0.75, 8: 0.9, 9: 1.1, 10: 1.6, 11: 1.4, 12: 1.1}
# Planted profiles drive the demo questions; 'mid' and 'sme' are ordinary customers
PROFILES = [("enterprise", 10), ("grower", 7), ("headline", 6), ("headline_decoy", 4), ("unpaid3", 8),
            ("dormant_big", 6), ("dormant_small", 6), ("mid", 45), ("sme", 128)]
