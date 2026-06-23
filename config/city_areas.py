"""Major areas/neighborhoods for Indian cities.

Used by the scraper to search within specific city zones when the main
city search doesn't yield enough results. Each city has its top areas
listed in order of importance/population density.
"""

CITY_AREAS: dict[str, list[str]] = {
    "indore": [
        "vijay nagar", "palasia", "rajwada", "rau", "bhanwarkua",
        "nipania", "kanadiya", "scheme 54", "lasudia", "ab road",
        "mhow", "pithampur", "manglia", "khajrana", "annapurna",
        "bhawarkua", "sudama nagar", "tejaji nagar", "pardeshipura",
        "malviya nagar", "bhagirathpura", "banganga",
    ],
    "delhi": [
        "connaught place", "lajpat nagar", "karol bagh", "saket",
        "dwarka", "rohini", "pitampura", "janakpuri", "preet vihar",
        "nehru place", "vasant kunj", "south extension", "hauz khas",
        "greater kailash", "model town", "uttam nagar", "mayur vihar",
        "shahdara", "dilshad garden", "east delhi",
    ],
    "mumbai": [
        "andheri", "bandra", "dadar", "kurla", "borivali",
        "thane", "malad", "goregaon", "kandivali", "vikhroli",
        "powai", "mulund", "ghatkopar", "chembur", "wadala",
        "lower parel", "worli", "juhu", "santacruz", "vile parle",
    ],
    "bangalore": [
        "koramangala", "indiranagar", "whitefield", "jp nagar",
        "hsr layout", "electronic city", "marathahalli", "hebbal",
        "jayanagar", "rajajinagar", "malleshwaram", "yelahanka",
        "bannerghatta road", "sarjapur road", "outer ring road",
        "btm layout", "bellandur", "mahadevapura", "kr puram",
    ],
    "hyderabad": [
        "hitech city", "banjara hills", "jubilee hills", "gachibowli",
        "secunderabad", "kukatpally", "dilsukhnagar", "ameerpet",
        "madhapur", "kondapur", "manikonda", "lb nagar",
        "nizampet", "miyapur", "uppal", "nacharam",
    ],
    "pune": [
        "koregaon park", "kothrud", "hadapsar", "viman nagar",
        "hinjewadi", "baner", "aundh", "pimpri", "chinchwad",
        "wakad", "deccan", "camp", "shivajinagar", "swargate",
        "kondhwa", "katraj", "bibwewadi", "nigdi",
    ],
    "chennai": [
        "t nagar", "anna nagar", "adyar", "velachery", "tambaram",
        "porur", "perambur", "guindy", "chromepet", "sholinganallur",
        "omr", "ecr", "besant nagar", "kodambakkam", "vadapalani",
        "teynampet", "mylapore", "nungambakkam",
    ],
    "kolkata": [
        "salt lake", "new town", "park street", "ballygunge",
        "gariahat", "howrah", "dum dum", "barasat", "behala",
        "jadavpur", "tollygunge", "alipore", "ultadanga",
        "garia", "kasba", "santoshpur",
    ],
    "ahmedabad": [
        "sg highway", "bopal", "satellite", "vastrapur",
        "navrangpura", "maninagar", "gota", "chandkheda",
        "thaltej", "paldi", "ellisbridge", "vejalpur",
        "prahlad nagar", "south bopal", "ranip",
    ],
    "jaipur": [
        "malviya nagar", "vaishali nagar", "mansarovar",
        "c scheme", "tonk road", "ajmer road", "sikar road",
        "sodala", "bais godam", "civil lines", "raja park",
        "jagatpura", "sanganer", "sitapura",
    ],
    "bhopal": [
        "mp nagar", "arera colony", "new market", "hoshangabad road",
        "kolar road", "misrod", "berasia road", "ayodhya bypass",
        "govindpura", "karond", "bairagarh", "katara hills",
    ],
    "nagpur": [
        "dharampeth", "sadar", "sitabuldi", "mahal",
        "pratap nagar", "manewada", "besa", "hingna road",
        "wardha road", "katol road", "amravati road",
    ],
    "surat": [
        "vesu", "adajan", "katargam", "udhna",
        "piplod", "pal", "athwa", "rander",
        "varachha", "parle point", "bhatar",
    ],
    "lucknow": [
        "gomti nagar", "hazratganj", "aliganj", "indira nagar",
        "alambagh", "ashiyana", "chinhat", "sushant golf city",
        "jankipuram", "mahanagar", "rajajipuram",
    ],
}

# Fallback generic areas for cities not in the above dict
GENERIC_AREAS = [
    "city center", "old city", "new area", "main market",
    "railway station area", "bus stand area", "sector 1", "sector 2",
]


def get_city_areas(location: str) -> list[str]:
    """Return list of areas for a given city/location string."""
    location_lower = location.strip().lower()
    # Try exact match first
    if location_lower in CITY_AREAS:
        return CITY_AREAS[location_lower]
    # Try partial match (e.g. 'indore, mp' -> 'indore')
    for city, areas in CITY_AREAS.items():
        if city in location_lower:
            return areas
    # Return generic fallback
    return GENERIC_AREAS
