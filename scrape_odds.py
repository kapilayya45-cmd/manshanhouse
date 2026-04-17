import requests
from bs4 import BeautifulSoup

url = "https://www.sportsbet.com.au/betting/cricket/the-ashes/australia-v-england-9928292"

response = requests.get(url)
soup = BeautifulSoup(response.text, 'html.parser')

# Find first 3 outcome labels and their corresponding odds (match result only)
labels = soup.select('[data-automation-id$="-three-outcome-label"]')[:3]
odds = soup.select('[data-automation-id$="-three-outcome-text"]')[:3]

# Calculate raw implied probabilities
raw_probs = [1 / float(odd.text) for odd in odds]
total = sum(raw_probs)

# Normalize to remove overround
for label, odd, raw_prob in zip(labels, odds, raw_probs):
    likelihood = raw_prob / total * 100
    print(f"{label.text}: {odd.text} ({likelihood:.1f}%)")
