"""City area mappings for targeted local searches within major Indian cities."""

from __future__ import annotations

_CITY_AREAS: dict[str, list[str]] = {
    "mumbai": [
        "Andheri", "Bandra", "Borivali", "Dadar", "Ghatkopar", "Juhu",
        "Kandivali", "Kurla", "Malad", "Powai", "Thane", "Vile Parle",
        "Worli", "Chembur", "Goregaon", "Mira Road", "Navi Mumbai",
        "Vasai", "Virar", "Dharavi",
    ],
    "delhi": [
        "Connaught Place", "Dwarka", "Janakpuri", "Karol Bagh", "Lajpat Nagar",
        "Nehru Place", "Pitampura", "Rohini", "Saket", "South Extension",
        "Vasant Kunj", "Rajouri Garden", "Preet Vihar", "Shahdara", "Noida",
        "Gurgaon", "Faridabad", "Ghaziabad", "Greater Noida",
    ],
    "bangalore": [
        "Whitefield", "Koramangala", "Indiranagar", "Jayanagar", "JP Nagar",
        "HSR Layout", "Electronic City", "Marathahalli", "Rajajinagar",
        "Malleshwaram", "Yelahanka", "Hebbal", "Banashankari", "Vijayanagar",
        "BTM Layout", "Sarjapur Road", "Bellandur", "Bannerghatta Road",
    ],
    "hyderabad": [
        "Banjara Hills", "Jubilee Hills", "Secunderabad", "Hitech City",
        "Gachibowli", "Kukatpally", "Dilsukhnagar", "LB Nagar",
        "Uppal", "Kondapur", "Ameerpet", "Begumpet", "Madhapur",
        "Miyapur", "Manikonda",
    ],
    "chennai": [
        "Anna Nagar", "T Nagar", "Adyar", "Velachery", "Tambaram",
        "Porur", "Chromepet", "Guindy", "Mylapore", "Perambur",
        "Royapettah", "Kodambakkam", "Nungambakkam", "Egmore",
        "Ambattur", "Avadi",
    ],
    "kolkata": [
        "Park Street", "Salt Lake", "New Town", "Ballygunge", "Behala",
        "Howrah", "Barasat", "Dum Dum", "Jadavpur", "Tollygunge",
        "Kasba", "Ultadanga", "Phoolbagan",
    ],
    "pune": [
        "Koregaon Park", "Kothrud", "Hadapsar", "Wakad", "Baner",
        "Aundh", "Hinjewadi", "Viman Nagar", "Pimple Saudagar",
        "Magarpatta", "Kharadi", "Swargate", "Deccan", "Camp",
        "Yerawada", "Pimpri", "Chinchwad",
    ],
    "ahmedabad": [
        "Navrangpura", "CG Road", "Satellite", "Maninagar", "Vastrapur",
        "Prahlad Nagar", "Bodakdev", "Thaltej", "Chandkheda", "Gota",
        "Naranpura", "Iskon", "SG Highway", "Bopal",
    ],
    "jaipur": [
        "Malviya Nagar", "Vaishali Nagar", "C Scheme", "MI Road",
        "Tonk Road", "Mansarovar", "Jagatpura", "Sitapura",
        "Sanganer", "Sodala", "Bani Park", "Civil Lines",
    ],
    "indore": [
        "Vijay Nagar", "Palasia", "MG Road", "Rau", "Rajwada",
        "Pipliyahana", "Super Corridor", "Bhawarkuan", "Bypass",
        "Sapna Sangeeta", "Bicholi Mardana", "Lasudia",
    ],
    "surat": [
        "Adajan", "Athwa", "City Light", "Pal", "Varachha",
        "Katargam", "Rander", "Udhna", "Piplod", "Vesu",
    ],
    "lucknow": [
        "Hazratganj", "Gomti Nagar", "Alambagh", "Aliganj", "Mahanagar",
        "Indira Nagar", "Rajajipuram", "Vikas Nagar", "Chinhat",
    ],
    "nagpur": [
        "Civil Lines", "Dharampeth", "Sadar", "Sitabuldi", "Gandhibagh",
        "Wardhaman Nagar", "Bajaj Nagar", "Ambazari", "Hingna",
    ],
    "bhopal": [
        "MP Nagar", "Arera Colony", "Kolar Road", "Bairagarh", "Habibganj",
        "Govindpura", "Shahpura", "TT Nagar", "Shyamla Hills",
    ],
    "kochi": [
        "Ernakulam", "Kakkanad", "Edapally", "Aluva", "Tripunithura",
        "Vyttila", "Palarivattom", "Fort Kochi", "Panampilly Nagar",
    ],
    "visakhapatnam": [
        "Dwaraka Nagar", "MVP Colony", "Gajuwaka", "Rushikonda",
        "Madhurawada", "Seethammadhara", "Bheemunipatnam",
    ],
}


def get_city_areas(city: str) -> list[str]:
    """Return area list for *city*, or [] if unknown."""
    return _CITY_AREAS.get(city.strip().lower(), [])
