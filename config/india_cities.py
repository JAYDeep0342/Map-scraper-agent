"""Comprehensive India locations for maximum lead coverage.

Contains:
- All 28 states + 8 Union Territories
- 600+ cities and districts across India
- Ordered by population/importance for best results first
"""

# All Indian States and Union Territories
INDIA_STATES = [
    "Uttar Pradesh", "Maharashtra", "Bihar", "West Bengal", "Madhya Pradesh",
    "Rajasthan", "Tamil Nadu", "Karnataka", "Gujarat", "Andhra Pradesh",
    "Odisha", "Telangana", "Kerala", "Jharkhand", "Assam",
    "Punjab", "Chhattisgarh", "Haryana", "Uttarakhand", "Himachal Pradesh",
    "Tripura", "Meghalaya", "Manipur", "Nagaland", "Goa",
    "Arunachal Pradesh", "Mizoram", "Sikkim",
    "Delhi", "Jammu and Kashmir", "Ladakh",
    "Chandigarh", "Puducherry", "Andaman and Nicobar Islands",
]

# All major Indian cities and districts — 600+ locations
INDIA_CITIES = [
    # Tier 1 — Major metros
    "Delhi", "Mumbai", "Bangalore", "Hyderabad", "Chennai",
    "Kolkata", "Pune", "Ahmedabad", "Surat", "Jaipur",
    "Lucknow", "Kanpur", "Nagpur", "Indore", "Thane",
    "Bhopal", "Visakhapatnam", "Pimpri Chinchwad", "Patna", "Vadodara",

    # Tier 2 — Large cities
    "Ghaziabad", "Ludhiana", "Agra", "Nashik", "Faridabad",
    "Meerut", "Rajkot", "Kalyan", "Varanasi", "Srinagar",
    "Aurangabad", "Dhanbad", "Amritsar", "Allahabad", "Ranchi",
    "Howrah", "Coimbatore", "Jabalpur", "Gwalior", "Vijayawada",
    "Jodhpur", "Madurai", "Raipur", "Kota", "Guwahati",
    "Chandigarh", "Solapur", "Hubli", "Dharwad", "Bareilly",
    "Moradabad", "Mysore", "Tiruppur", "Gurgaon", "Aligarh",
    "Thiruvananthapuram", "Jalandhar", "Bhubaneswar", "Salem", "Warangal",
    "Mira Bhayandar", "Jalgaon", "Guntur", "Bhiwandi", "Saharanpur",
    "Gorakhpur", "Bikaner", "Amravati", "Noida", "Jamshedpur",
    "Bhilai", "Cuttack", "Firozabad", "Kochi", "Bhavnagar",
    "Dehradun", "Durgapur", "Asansol", "Nanded", "Kolapur",
    "Ajmer", "Gulbarga", "Jamnagar", "Ujjain", "Loni",
    "Siliguri", "Jhansi", "Ulhasnagar", "Nellore", "Jammu",
    "Sangli", "Belgaum", "Mangalore", "Ambattur", "Tirunelveli",
    "Malegaon", "Gaya", "Tiruchirappalli", "Udaipur", "Kakinada",

    # Tier 3 — Medium cities
    "Maheshtala", "Davanagere", "Kozhikode", "Akola", "Kurnool",
    "Rajpur Sonarpur", "Bokaro", "South Dumdum", "Bellary", "Patiala",
    "Gopalpur", "Agartala", "Bhagalpur", "Muzaffarnagar", "Bhatpara",
    "Panihati", "Latur", "Dhule", "Rohtak", "Sagar",
    "Korba", "Bhilwara", "Brahmapur", "Muzaffarpur", "Ahmednagar",
    "Mathura", "Kolhapur", "Avadi", "Kadapa", "Kamarhati",
    "Bilaspur", "Shahjahanpur", "Bijapur", "Rampur", "Shambhajinagar",
    "Shimoga", "Chandrapur", "Junagadh", "Thrissur", "Alwar",
    "Bardhaman", "Kulti", "Nizamabad", "Parbhani", "Tumkur",
    "Hisar", "Ozhukarai", "Bihar Sharif", "Panipat", "Darbhanga",
    "Bally", "Aizawl", "Dewas", "Ichalkaranji", "Karnal",
    "Bathinda", "Jalna", "Barasat", "Tiruvottiyur", "Imphal",
    "Ratlam", "Hapur", "Arrah", "Karimnagar", "Anantapur",
    "Etawah", "Ambernath", "North Dum Dum", "Bharatpur", "Begusarai",
    "New Delhi", "Gandhidham", "Baranagar", "Tiruvottiyur", "Pondicherry",
    "Sikar", "Thoothukudi", "Rourkela", "Mirzapur", "Rajahmundry",
    "Katni", "Sambhal", "Satna", "Orai", "Bidar",

    # Rajasthan districts
    "Alwar", "Barmer", "Bhilwara", "Bikaner", "Bundi",
    "Chittorgarh", "Churu", "Dausa", "Dholpur", "Dungarpur",
    "Hanumangarh", "Jaisalmer", "Jhalawar", "Jhunjhunu", "Karauli",
    "Nagaur", "Pali", "Pratapgarh", "Rajsamand", "Sawai Madhopur",
    "Sikar", "Sirohi", "Sri Ganganagar", "Tonk", "Udaipur",

    # Uttar Pradesh major districts
    "Agra", "Aligarh", "Allahabad", "Ambedkar Nagar", "Amethi",
    "Amroha", "Auraiya", "Azamgarh", "Baghpat", "Bahraich",
    "Ballia", "Balrampur", "Banda", "Barabanki", "Bareilly",
    "Basti", "Bijnor", "Budaun", "Bulandshahr", "Chandauli",
    "Chitrakoot", "Deoria", "Etah", "Etawah", "Faizabad",
    "Farrukhabad", "Fatehpur", "Firozabad", "Gautam Buddh Nagar", "Ghazipur",
    "Gonda", "Gorakhpur", "Hamirpur", "Hapur", "Hardoi",
    "Hathras", "Jalaun", "Jaunpur", "Jhansi", "Kannauj",
    "Kanpur Dehat", "Kanpur Nagar", "Kasganj", "Kaushambi", "Kheri",
    "Kushinagar", "Lalitpur", "Lucknow", "Maharajganj", "Mahoba",
    "Mainpuri", "Mathura", "Mau", "Meerut", "Mirzapur",
    "Moradabad", "Muzaffarnagar", "Pilibhit", "Pratapgarh", "Prayagraj",
    "Rae Bareli", "Rampur", "Saharanpur", "Sambhal", "Sant Kabir Nagar",
    "Shahjahanpur", "Shamli", "Shravasti", "Siddharthnagar", "Sitapur",
    "Sonbhadra", "Sultanpur", "Unnao", "Varanasi",

    # Maharashtra districts
    "Ahmednagar", "Akola", "Amravati", "Aurangabad", "Beed",
    "Bhandara", "Buldhana", "Chandrapur", "Dhule", "Gadchiroli",
    "Gondia", "Hingoli", "Jalgaon", "Jalna", "Kolhapur",
    "Latur", "Mumbai City", "Mumbai Suburban", "Nagpur", "Nanded",
    "Nandurbar", "Nashik", "Osmanabad", "Palghar", "Parbhani",
    "Pune", "Raigad", "Ratnagiri", "Sangli", "Satara",
    "Sindhudurg", "Solapur", "Thane", "Wardha", "Washim", "Yavatmal",

    # Madhya Pradesh districts
    "Agar Malwa", "Alirajpur", "Anuppur", "Ashoknagar", "Balaghat",
    "Barwani", "Betul", "Bhind", "Bhopal", "Burhanpur",
    "Chhatarpur", "Chhindwara", "Damoh", "Datia", "Dewas",
    "Dhar", "Dindori", "Guna", "Gwalior", "Harda",
    "Hoshangabad", "Indore", "Jabalpur", "Jhabua", "Katni",
    "Khandwa", "Khargone", "Mandla", "Mandsaur", "Morena",
    "Narsinghpur", "Neemuch", "Niwari", "Panna", "Raisen",
    "Rajgarh", "Ratlam", "Rewa", "Sagar", "Satna",
    "Sehore", "Seoni", "Shahdol", "Shajapur", "Sheopur",
    "Shivpuri", "Sidhi", "Singrauli", "Tikamgarh", "Ujjain",
    "Umaria", "Vidisha",

    # Gujarat districts
    "Ahmedabad", "Amreli", "Anand", "Aravalli", "Banaskantha",
    "Bharuch", "Bhavnagar", "Botad", "Chhota Udaipur", "Dahod",
    "Dang", "Devbhoomi Dwarka", "Gandhinagar", "Gir Somnath", "Jamnagar",
    "Junagadh", "Kutch", "Kheda", "Mahisagar", "Mehsana",
    "Morbi", "Narmada", "Navsari", "Panchmahal", "Patan",
    "Porbandar", "Rajkot", "Sabarkantha", "Surat", "Surendranagar",
    "Tapi", "Vadodara", "Valsad",

    # Karnataka districts
    "Bagalkot", "Bangalore Rural", "Bangalore Urban", "Belgaum", "Bellary",
    "Bidar", "Bijapur", "Chamarajanagar", "Chikballapur", "Chikmagalur",
    "Chitradurga", "Dakshina Kannada", "Davanagere", "Dharwad", "Gadag",
    "Hassan", "Haveri", "Kodagu", "Kolar", "Koppal",
    "Mandya", "Mysore", "Raichur", "Ramanagara", "Shimoga",
    "Tumkur", "Udupi", "Uttara Kannada", "Vijayapura", "Yadgir",

    # Tamil Nadu districts
    "Ariyalur", "Chengalpattu", "Chennai", "Coimbatore", "Cuddalore",
    "Dharmapuri", "Dindigul", "Erode", "Kallakurichi", "Kancheepuram",
    "Kanyakumari", "Karur", "Krishnagiri", "Madurai", "Nagapattinam",
    "Namakkal", "Nilgiris", "Perambalur", "Pudukkottai", "Ramanathapuram",
    "Ranipet", "Salem", "Sivaganga", "Tenkasi", "Thanjavur",
    "Theni", "Thoothukudi", "Tiruchirappalli", "Tirunelveli", "Tirupathur",
    "Tiruppur", "Tiruvallur", "Tiruvannamalai", "Tiruvarur", "Vellore",
    "Viluppuram", "Virudhunagar",

    # Kerala districts
    "Alappuzha", "Ernakulam", "Idukki", "Kannur", "Kasaragod",
    "Kollam", "Kottayam", "Kozhikode", "Malappuram", "Palakkad",
    "Pathanamthitta", "Thiruvananthapuram", "Thrissur", "Wayanad",

    # West Bengal districts
    "Alipurduar", "Bankura", "Birbhum", "Cooch Behar", "Dakshin Dinajpur",
    "Darjeeling", "Hooghly", "Howrah", "Jalpaiguri", "Jhargram",
    "Kalimpong", "Kolkata", "Malda", "Murshidabad", "Nadia",
    "North 24 Parganas", "Paschim Bardhaman", "Paschim Medinipur",
    "Purba Bardhaman", "Purba Medinipur", "Purulia",
    "South 24 Parganas", "Uttar Dinajpur",

    # Bihar districts
    "Araria", "Arwal", "Aurangabad", "Banka", "Begusarai",
    "Bhagalpur", "Bhojpur", "Buxar", "Darbhanga", "East Champaran",
    "Gaya", "Gopalganj", "Jamui", "Jehanabad", "Kaimur",
    "Katihar", "Khagaria", "Kishanganj", "Lakhisarai", "Madhepura",
    "Madhubani", "Munger", "Muzaffarpur", "Nalanda", "Nawada",
    "Patna", "Purnia", "Rohtas", "Saharsa", "Samastipur",
    "Saran", "Sheikhpura", "Sheohar", "Sitamarhi", "Siwan",
    "Supaul", "Vaishali", "West Champaran",

    # Rajasthan (additional)
    "Ajmer", "Jodhpur", "Kota", "Bharatpur", "Pali",

    # Andhra Pradesh districts
    "Anantapur", "Chittoor", "East Godavari", "Guntur", "Kadapa",
    "Krishna", "Kurnool", "Nellore", "Prakasam", "Srikakulam",
    "Visakhapatnam", "Vizianagaram", "West Godavari",

    # Telangana districts
    "Adilabad", "Bhadradri Kothagudem", "Hyderabad", "Jagtial", "Jangaon",
    "Jayashankar Bhupalpally", "Jogulamba Gadwal", "Kamareddy", "Karimnagar",
    "Khammam", "Komaram Bheem Asifabad", "Mahabubabad", "Mahabubnagar",
    "Mancherial", "Medak", "Medchal", "Mulugu", "Nagarkurnool",
    "Nalgonda", "Narayanpet", "Nirmal", "Nizamabad", "Peddapalli",
    "Rajanna Sircilla", "Rangareddy", "Sangareddy", "Siddipet",
    "Suryapet", "Vikarabad", "Wanaparthy", "Warangal Rural",
    "Warangal Urban", "Yadadri Bhuvanagiri",

    # Odisha districts
    "Angul", "Balangir", "Balasore", "Bargarh", "Bhadrak",
    "Boudh", "Cuttack", "Deogarh", "Dhenkanal", "Gajapati",
    "Ganjam", "Jagatsinghpur", "Jajpur", "Jharsuguda", "Kalahandi",
    "Kandhamal", "Kendrapara", "Kendujhar", "Khordha", "Koraput",
    "Malkangiri", "Mayurbhanj", "Nabarangpur", "Nayagarh", "Nuapada",
    "Puri", "Rayagada", "Sambalpur", "Sonepur", "Sundargarh",

    # Jharkhand districts
    "Bokaro", "Chatra", "Deoghar", "Dhanbad", "Dumka",
    "East Singhbhum", "Garhwa", "Giridih", "Godda", "Gumla",
    "Hazaribagh", "Jamtara", "Khunti", "Koderma", "Latehar",
    "Lohardaga", "Pakur", "Palamu", "Ramgarh", "Ranchi",
    "Sahebganj", "Seraikela Kharsawan", "Simdega", "West Singhbhum",

    # Assam districts
    "Baksa", "Barpeta", "Biswanath", "Bongaigaon", "Cachar",
    "Charaideo", "Chirang", "Darrang", "Dhemaji", "Dhubri",
    "Dibrugarh", "Dima Hasao", "Goalpara", "Golaghat", "Guwahati",
    "Hailakandi", "Hojai", "Jorhat", "Kamrup", "Karbi Anglong",
    "Karimganj", "Kokrajhar", "Lakhimpur", "Majuli", "Morigaon",
    "Nagaon", "Nalbari", "Sivasagar", "Sonitpur", "Tinsukia",

    # Punjab districts
    "Amritsar", "Barnala", "Bathinda", "Faridkot", "Fatehgarh Sahib",
    "Fazilka", "Ferozepur", "Gurdaspur", "Hoshiarpur", "Jalandhar",
    "Kapurthala", "Ludhiana", "Malerkotla", "Mansa", "Moga",
    "Mohali", "Muktsar", "Pathankot", "Patiala", "Rupnagar",
    "Sangrur", "Shaheed Bhagat Singh Nagar", "Tarn Taran",

    # Haryana districts
    "Ambala", "Bhiwani", "Charkhi Dadri", "Faridabad", "Fatehabad",
    "Gurgaon", "Hisar", "Jhajjar", "Jind", "Kaithal",
    "Karnal", "Kurukshetra", "Mahendragarh", "Nuh", "Palwal",
    "Panchkula", "Panipat", "Rewari", "Rohtak", "Sirsa",
    "Sonipat", "Yamunanagar",

    # Himachal Pradesh districts
    "Bilaspur", "Chamba", "Hamirpur", "Kangra", "Kinnaur",
    "Kullu", "Lahaul Spiti", "Mandi", "Shimla", "Sirmaur",
    "Solan", "Una",

    # Uttarakhand districts
    "Almora", "Bageshwar", "Chamoli", "Champawat", "Dehradun",
    "Haridwar", "Nainital", "Pauri Garhwal", "Pithoragarh", "Rudraprayag",
    "Tehri Garhwal", "Udham Singh Nagar", "Uttarkashi",

    # Chhattisgarh districts
    "Balod", "Baloda Bazar", "Balrampur", "Bastar", "Bemetara",
    "Bijapur", "Bilaspur", "Dantewada", "Dhamtari", "Durg",
    "Gariaband", "Janjgir Champa", "Jashpur", "Kabirdham",
    "Kanker", "Kondagaon", "Korba", "Koriya", "Mahasamund",
    "Mungeli", "Narayanpur", "Raigarh", "Raipur", "Rajnandgaon",
    "Sukma", "Surajpur", "Surguja",

    # Goa districts
    "North Goa", "South Goa", "Panaji", "Margao", "Vasco",

    # Jammu & Kashmir
    "Anantnag", "Bandipora", "Baramulla", "Budgam", "Doda",
    "Ganderbal", "Jammu", "Kathua", "Kishtwar", "Kulgam",
    "Kupwara", "Poonch", "Pulwama", "Rajouri", "Ramban",
    "Reasi", "Samba", "Shopian", "Srinagar", "Udhampur",

    # Tripura districts
    "Dhalai", "Gomati", "Khowai", "North Tripura", "Sepahijala",
    "Sipahijala", "South Tripura", "Unakoti", "West Tripura",

    # Meghalaya districts
    "East Garo Hills", "East Jaintia Hills", "East Khasi Hills",
    "North Garo Hills", "Ri Bhoi", "South Garo Hills",
    "South West Garo Hills", "South West Khasi Hills", "West Garo Hills",
    "West Jaintia Hills", "West Khasi Hills",

    # Additional important towns
    "Muzaffarnagar", "Saharanpur", "Meerut", "Ghaziabad", "Hapur",
    "Bulandshahr", "Aligarh", "Mathura", "Agra", "Firozabad",
    "Etawah", "Kanpur", "Lucknow", "Sultanpur", "Faizabad",
    "Gorakhpur", "Deoria", "Basti", "Varanasi", "Mirzapur",
    "Allahabad", "Jhansi", "Banda", "Chitrakoot", "Lalitpur",
    "Hamirpur", "Mahoba", "Jalaun",
]
