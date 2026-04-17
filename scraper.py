#!/usr/bin/env python3
"""
Scraper module that scrapes cricket match odds and live scores.
Stores results in a Postgres database. Designed to be importable by app.py.
"""

import re
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
from db import get_db, get_cursor


def get_last_snapshot(match_id):
    """Get the most recent snapshot data for a match (for deduplication)."""
    with get_db() as conn:
        with get_cursor(conn) as c:
            # Get the latest snapshot
            c.execute('''
                SELECT id, match_status
                FROM snapshots
                WHERE match_id = %s
                ORDER BY id DESC
                LIMIT 1
            ''', (match_id,))
            snapshot = c.fetchone()

            if not snapshot:
                return None

            snapshot_id = snapshot['id']

            # Get odds for this snapshot
            c.execute('SELECT outcome, odds FROM odds WHERE snapshot_id = %s', (snapshot_id,))
            odds = {row['outcome']: row['odds'] for row in c.fetchall()}

            # Get innings for this snapshot
            c.execute('SELECT runs, wickets, overs FROM innings WHERE snapshot_id = %s', (snapshot_id,))
            innings_rows = c.fetchall()

            cumulative_overs = sum(row['overs'] or 0 for row in innings_rows)
            total_wickets = sum(row['wickets'] or 0 for row in innings_rows)

            return {
                'odds': odds,
                'cumulative_overs': cumulative_overs,
                'total_wickets': total_wickets,
                'status': snapshot['match_status']
            }


def is_duplicate(odds_data, cricket_data, previous):
    """Check if current scrape data is identical to previous snapshot."""
    if not previous:
        return False

    # Compare odds
    current_odds = {}
    if odds_data:
        current_odds = {outcome: data['odds'] for outcome, data in odds_data.items()}

    if current_odds != previous['odds']:
        return False

    # Compare match state
    if cricket_data:
        cumulative_overs = sum(inn.get('overs') or 0 for inn in cricket_data.get('innings', []))
        total_wickets = sum(inn.get('wickets') or 0 for inn in cricket_data.get('innings', []))
        status = cricket_data.get('status')
    else:
        cumulative_overs = 0
        total_wickets = 0
        status = None

    if cumulative_overs != previous['cumulative_overs']:
        return False

    if total_wickets != previous['total_wickets']:
        return False

    if status != previous['status']:
        return False

    return True


def get_active_events():
    """Load active events from the database."""
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('''
                SELECT id, name, event_type, odds_url, score_url, cricbuzz_url, scorecard_url, score_source, outcomes, allowed_outcomes
                FROM events
                WHERE active = true AND archived = false
            ''')
            rows = c.fetchall()

    events = []
    for row in rows:
        # Postgres JSONB is auto-parsed, no need for json.loads
        allowed = row['allowed_outcomes'] or []
        events.append({
            'id': row['id'],
            'name': row['name'],
            'match_type': row['event_type'],
            'odds_url': row['odds_url'],
            'cricket_url': row['score_url'],
            'cricbuzz_url': row['cricbuzz_url'],
            'scorecard_url': row['scorecard_url'],
            'score_source': row['score_source'] or 'cricinfo',
            'outcomes': row['outcomes'] or 2,
            'allowed_outcomes': allowed,
        })
    return events


def get_match_id(match_name):
    """Get the database ID for a match by name."""
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('SELECT id FROM matches WHERE name = %s', (match_name,))
            result = c.fetchone()
    return result['id'] if result else None


def scrape_odds(url, num_outcomes=2, allowed_outcomes=None):
    """Scrape match odds from Sportsbet."""
    import os

    # Check if we should use an Australian proxy
    proxy_url = os.environ.get('SPORTSBET_PROXY_URL')
    proxy_token = os.environ.get('SPORTSBET_PROXY_TOKEN')

    if proxy_url and proxy_token:
        # Use the Australian proxy
        proxy_request_url = f"{proxy_url}?token={proxy_token}&url={url}"
        response = requests.get(proxy_request_url, timeout=20)
        print(f"    [DEBUG] Odds: Using proxy")
    else:
        # Direct request
        response = requests.get(url, timeout=15)

    soup = BeautifulSoup(response.text, 'html.parser')

    labels = []
    odds_elements = []

    # Try three-outcome pattern (Test matches with draw)
    if num_outcomes == 3:
        labels = soup.select('[data-automation-id$="-three-outcome-label"]')
        odds_elements = soup.select('[data-automation-id$="-three-outcome-text"]')

    # Try two-outcome pattern (T20/ODI without draw)
    if not labels or not odds_elements:
        labels = soup.select('[data-automation-id$="-two-outcome-label"]')
        odds_elements = soup.select('[data-automation-id$="-two-outcome-text"]')

    # Try list-outcome pattern (live matches)
    if not labels or not odds_elements:
        labels = soup.select('[data-automation-id$="-list-outcome-name"]')
        odds_elements = soup.select('[data-automation-id$="-list-outcome-text"]')

    if not labels or not odds_elements:
        print(f"    [DEBUG] Odds: No labels/odds found on page")
        return None

    print(f"    [DEBUG] Odds: Found {len(labels)} labels, {len(odds_elements)} odds")
    # Build result, filtering by allowed outcomes if specified
    result = {}
    for label, odd in zip(labels, odds_elements):
        outcome_name = label.text.strip()

        # Skip if not in allowed list
        if allowed_outcomes and outcome_name not in allowed_outcomes:
            continue

        result[outcome_name] = {
            'odds': float(odd.text),
        }

    if not result:
        return None

    # Calculate implied probabilities only for the filtered results
    raw_probs = [1 / data['odds'] for data in result.values()]
    total = sum(raw_probs)

    for outcome, data in result.items():
        raw_prob = 1 / data['odds']
        data['implied_probability'] = round(raw_prob / total * 100, 2)

    return result


def scrape_cricbuzz_scorecard(url):
    """
    Scrape cricket data from Cricbuzz scorecard page.

    Uses /live-cricket-scorecard/ URL which has cleaner, more explicit data format.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    }

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    result = {
        'status': None,
        'stage': None,
        'state': None,
        'teams': [],
        'innings': []
    }

    # Extract match status from page text
    page_text = soup.get_text(separator=' ', strip=True)

    # Look for status patterns like "Day 4: Lunch Break - New Zealand lead by 190 runs"
    status_match = re.search(r'(Day \d+:[^|]+?)(?:\s*(?:NZ|WI|AUS|ENG|IND|SA|PAK|SL|BAN|ZIM|AFG|IRE)\s+(?:1st|2nd))', page_text)
    if status_match:
        result['status'] = status_match.group(1).strip()

    # Fallback: look for common status patterns
    if not result['status']:
        for pattern in [
            r'(Day \d+:\s*[^|]+?(?:lead|trail|need|won|drawn|tied)[^|]*)',
            r'(Day \d+:\s*(?:Stumps|Lunch|Tea|Dinner|Live)[^|]*)',
        ]:
            match = re.search(pattern, page_text, re.IGNORECASE)
            if match:
                result['status'] = match.group(1).strip()
                break

    # Determine match state from status
    status_lower = (result['status'] or '').lower()
    if 'stumps' in status_lower:
        result['state'] = 'STUMPS'
    elif 'tea' in status_lower:
        result['state'] = 'TEA'
    elif 'lunch' in status_lower:
        result['state'] = 'LUNCH'
    elif 'dinner' in status_lower:
        result['state'] = 'DINNER'
    elif any(x in status_lower for x in ['won', 'draw', 'drawn', 'tie', 'tied']):
        result['state'] = 'FINISHED'
    else:
        result['state'] = 'LIVE'

    # Extract innings data
    # Pattern: "Team Name Nth Innings Score-Wickets (Overs Ov)"
    # Examples:
    #   "New Zealand 1st Innings 575-8 (155 Ov)"
    #   "West Indies 1st Innings 420-10 (128.2 Ov)"
    innings_pattern = re.compile(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+'  # Team name (e.g., "New Zealand", "India")
        r'(1st|2nd)\s+Innings\s+'                # Innings number
        r'(\d+)-(\d+)\s+'                        # Runs-Wickets
        r'\((\d+(?:\.\d+)?)\s*Ov',               # Overs
        re.IGNORECASE
    )

    seen_innings = set()  # Track (team, innings_num) to avoid duplicates

    for elem in soup.find_all(string=re.compile(r'(1st|2nd)\s+Innings')):
        parent = elem.parent
        if parent and parent.parent:
            context = parent.parent.get_text(separator=' ', strip=True)

            for match in innings_pattern.finditer(context):
                team = match.group(1).strip()
                innings_ord = match.group(2)  # "1st" or "2nd"
                runs = int(match.group(3))
                wickets = int(match.group(4))
                overs = float(match.group(5))

                # Clean up team name - remove "Innings" if accidentally captured
                if team.lower().startswith('innings '):
                    team = team[8:].strip()
                if team.lower().endswith(' innings'):
                    team = team[:-8].strip()

                # Convert ordinal to number
                innings_num = 1 if innings_ord.lower() == '1st' else 2

                # Skip duplicates
                key = (team, innings_num)
                if key in seen_innings:
                    continue
                seen_innings.add(key)

                result['innings'].append({
                    'team': team,
                    'inning_number': innings_num,  # Team's innings (1 or 2)
                    'runs': runs,
                    'wickets': wickets,
                    'overs': overs
                })

    # Sort innings into proper match order
    # Test match order: Team1 1st, Team2 1st, Team1 2nd, Team2 2nd
    if len(result['innings']) >= 2:
        # Group by team
        teams = {}
        for inn in result['innings']:
            if inn['team'] not in teams:
                teams[inn['team']] = []
            teams[inn['team']].append(inn)

        if len(teams) == 2:
            team_names = list(teams.keys())

            # Determine who batted first:
            # - If one team has 2 innings and other has 1, the team with 1 batted FIRST
            #   (they haven't started their 2nd yet because the other team just finished batting)
            # - If both have same count, check which team's 1st innings is all out
            team_inn_counts = {t: len(inns) for t, inns in teams.items()}

            if team_inn_counts[team_names[0]] != team_inn_counts[team_names[1]]:
                # Team with fewer innings batted first (other team is currently batting their 2nd)
                first_team = min(team_names, key=lambda t: team_inn_counts[t])
                second_team = max(team_names, key=lambda t: team_inn_counts[t])
            else:
                # Same count - check if one team's 1st innings is all out (10 wickets)
                first_team = None
                for t in team_names:
                    for inn in teams[t]:
                        if inn['inning_number'] == 1 and inn['wickets'] == 10:
                            first_team = t
                            break
                    if first_team:
                        break

                if not first_team:
                    # Fallback: use page order
                    first_team = result['innings'][0]['team']

                second_team = [t for t in team_names if t != first_team][0]

            # Rebuild in proper match order
            ordered = []
            match_innings_num = 1

            # First team's 1st innings
            for inn in teams.get(first_team, []):
                if inn['inning_number'] == 1:
                    ordered.append({**inn, 'inning_number': match_innings_num})
                    match_innings_num += 1
                    break

            # Second team's 1st innings
            for inn in teams.get(second_team, []):
                if inn['inning_number'] == 1:
                    ordered.append({**inn, 'inning_number': match_innings_num})
                    match_innings_num += 1
                    break

            # First team's 2nd innings
            for inn in teams.get(first_team, []):
                if inn['inning_number'] == 2:
                    ordered.append({**inn, 'inning_number': match_innings_num})
                    match_innings_num += 1
                    break

            # Second team's 2nd innings
            for inn in teams.get(second_team, []):
                if inn['inning_number'] == 2:
                    ordered.append({**inn, 'inning_number': match_innings_num})
                    match_innings_num += 1
                    break

            result['innings'] = ordered

    print(f"    [DEBUG] Cricbuzz scorecard: status={result.get('status')}, state={result.get('state')}, innings={len(result.get('innings', []))}")
    return result


def scrape_cricbuzz(url):
    """Scrape live cricket data from Cricbuzz (legacy /live-cricket-scores/ page)."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    }

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    result = {
        'status': None,
        'stage': None,
        'state': None,
        'teams': [],
        'innings': []
    }

    # Find the main score header div
    # Pattern: "AUS 371 & 349 ENG 286 & 207 / 6 (63) CRR: 3.29 Day 4: Stumps - England need 228 runs"
    score_div = soup.find('div', class_=lambda c: c and 'flex' in c and 'flex-col' in c and 'gap-[5px]' in c)

    if not score_div:
        # Try alternative selector
        for div in soup.find_all('div'):
            text = div.get_text(separator=' ', strip=True)
            if re.search(r'Day \d+:', text) and '&' in text:
                score_div = div
                break

    if not score_div:
        return None

    full_text = score_div.get_text(separator=' ', strip=True)
    print(f"    [DEBUG] Cricbuzz raw text: {full_text[:200]}")

    # Extract status (e.g., "Day 4: Stumps - England need 228 runs")
    status_match = re.search(r'(Day \d+:.*?)$', full_text)
    if status_match:
        result['status'] = status_match.group(1).strip()

    # Determine match state from status
    status_lower = (result['status'] or '').lower()
    if 'stumps' in status_lower:
        result['state'] = 'STUMPS'
    elif 'tea' in status_lower:
        result['state'] = 'TEA'
    elif 'lunch' in status_lower:
        result['state'] = 'LUNCH'
    elif 'dinner' in status_lower:
        result['state'] = 'DINNER'
    elif any(x in status_lower for x in ['won', 'draw', 'tie']):
        result['state'] = 'FINISHED'
    else:
        result['state'] = 'LIVE'

    # Parse team scores
    # Format: "AUS 371 & 349" or "ENG 286 & 207 / 6 (63)"
    team_patterns = [
        # Team with two innings, second in progress: ENG 286 & 207 / 6 (63)
        r'([A-Z]{2,4})\s+(\d+)\s*&\s*(\d+)\s*/\s*(\d+)\s*\((\d+(?:\.\d+)?)\)',
        # Team with two completed innings: AUS 371 & 349
        r'([A-Z]{2,4})\s+(\d+)\s*&\s*(\d+)(?!\s*/)',
        # Team with one innings in progress: ENG 207 / 6 (63)
        r'([A-Z]{2,4})\s+(\d+)\s*/\s*(\d+)\s*\((\d+(?:\.\d+)?)\)',
        # Team with one completed innings: AUS 371
        r'([A-Z]{2,4})\s+(\d+)(?!\s*[&/])',
    ]

    # Team name mapping
    team_names = {
        'AUS': 'Australia', 'ENG': 'England', 'IND': 'India',
        'NZ': 'New Zealand', 'SA': 'South Africa', 'PAK': 'Pakistan',
        'WI': 'West Indies', 'SL': 'Sri Lanka', 'BAN': 'Bangladesh',
        'ZIM': 'Zimbabwe', 'AFG': 'Afghanistan', 'IRE': 'Ireland',
    }

    innings_num = 0
    processed_teams = set()

    # Pattern: NZ 575 / 8 d & 35 / 0 (11) - declared first innings, second in progress
    for match in re.finditer(r'([A-Z]{2,4})\s+(\d+)\s*/\s*(\d+)\s*d\s*&\s*(\d+)\s*/\s*(\d+)\s*\((\d+(?:\.\d+)?)\)', full_text):
        abbr, inn1_runs, inn1_wickets, inn2_runs, inn2_wickets, inn2_overs = match.groups()
        team_name = team_names.get(abbr, abbr)
        if team_name in processed_teams:
            continue
        processed_teams.add(team_name)
        innings_num += 1
        result['innings'].append({
            'team': team_name,
            'inning_number': innings_num,
            'runs': int(inn1_runs),
            'wickets': int(inn1_wickets),
            'overs': None  # Declared
        })
        innings_num += 1
        result['innings'].append({
            'team': team_name,
            'inning_number': innings_num,
            'runs': int(inn2_runs),
            'wickets': int(inn2_wickets),
            'overs': float(inn2_overs)
        })

    # Two innings with second in progress (no declaration): ENG 286 & 207 / 6 (63)
    for match in re.finditer(r'([A-Z]{2,4})\s+(\d+)\s*&\s*(\d+)\s*/\s*(\d+)\s*\((\d+(?:\.\d+)?)\)', full_text):
        abbr, inn1_runs, inn2_runs, inn2_wickets, inn2_overs = match.groups()
        team_name = team_names.get(abbr, abbr)
        if team_name in processed_teams:
            continue
        processed_teams.add(team_name)
        innings_num += 1
        result['innings'].append({
            'team': team_name,
            'inning_number': innings_num,
            'runs': int(inn1_runs),
            'wickets': 10,  # Completed innings
            'overs': None
        })
        innings_num += 1
        result['innings'].append({
            'team': team_name,
            'inning_number': innings_num,
            'runs': int(inn2_runs),
            'wickets': int(inn2_wickets),
            'overs': float(inn2_overs)
        })

    # Two completed innings (no current batting)
    for match in re.finditer(r'([A-Z]{2,4})\s+(\d+)\s*&\s*(\d+)(?!\s*/)', full_text):
        abbr, inn1_runs, inn2_runs = match.groups()
        team_name = team_names.get(abbr, abbr)
        if team_name in processed_teams:
            continue
        processed_teams.add(team_name)
        innings_num += 1
        result['innings'].append({
            'team': team_name,
            'inning_number': innings_num,
            'runs': int(inn1_runs),
            'wickets': 10,
            'overs': None
        })
        innings_num += 1
        result['innings'].append({
            'team': team_name,
            'inning_number': innings_num,
            'runs': int(inn2_runs),
            'wickets': 10,
            'overs': None
        })

    # One innings in progress (no &): ENG 207 / 6 (63)
    for match in re.finditer(r'([A-Z]{2,4})\s+(\d+)\s*/\s*(\d+)\s*\((\d+(?:\.\d+)?)\)', full_text):
        abbr, runs, wickets, overs = match.groups()
        team_name = team_names.get(abbr, abbr)
        if team_name in processed_teams:
            continue
        processed_teams.add(team_name)
        innings_num += 1
        result['innings'].append({
            'team': team_name,
            'inning_number': innings_num,
            'runs': int(runs),
            'wickets': int(wickets),
            'overs': float(overs)
        })

    # One completed innings (just runs, no wickets/overs): WI 420
    for match in re.finditer(r'([A-Z]{2,4})\s+(\d+)(?!\s*[/&])', full_text):
        abbr, runs = match.groups()
        team_name = team_names.get(abbr, abbr)
        if team_name in processed_teams:
            continue
        # Skip if this looks like it's part of "CRR: 3.18" or similar
        if abbr in ['CRR', 'RRR', 'REQ']:
            continue
        processed_teams.add(team_name)
        innings_num += 1
        result['innings'].append({
            'team': team_name,
            'inning_number': innings_num,
            'runs': int(runs),
            'wickets': 10,  # All out or declared
            'overs': None
        })

    # Re-order innings properly for Test matches
    # In Tests: Team1 Inn1, Team2 Inn1, Team1 Inn2, Team2 Inn2
    # We need to figure out which team batted first from the page order

    # Handle 3 innings case (one team has batted twice, other once)
    if len(result['innings']) == 3:
        teams_seen = []
        for inn in result['innings']:
            if inn['team'] not in teams_seen:
                teams_seen.append(inn['team'])

        if len(teams_seen) == 2:
            # Find which team has 2 innings (batting now in 2nd)
            team_innings_count = {}
            for inn in result['innings']:
                team_innings_count[inn['team']] = team_innings_count.get(inn['team'], 0) + 1

            team_with_two = [t for t, c in team_innings_count.items() if c == 2][0]
            team_with_one = [t for t, c in team_innings_count.items() if c == 1][0]

            # The team batting second in 1st innings bats first in 2nd innings
            # So team_with_one batted first overall
            team1_innings = [i for i in result['innings'] if i['team'] == team_with_one]
            team2_innings = [i for i in result['innings'] if i['team'] == team_with_two]

            new_innings = []
            # Team1's first innings
            if team1_innings:
                inn = team1_innings[0].copy()
                inn['inning_number'] = 1
                new_innings.append(inn)
            # Team2's first innings
            if team2_innings:
                inn = team2_innings[0].copy()
                inn['inning_number'] = 2
                new_innings.append(inn)
            # Team2's second innings (they're batting now, following on or normal order)
            if len(team2_innings) > 1:
                inn = team2_innings[1].copy()
                inn['inning_number'] = 3
                new_innings.append(inn)

            result['innings'] = new_innings

    elif len(result['innings']) == 4:
        # Get unique teams in order they appear
        teams_seen = []
        for inn in result['innings']:
            if inn['team'] not in teams_seen:
                teams_seen.append(inn['team'])

        if len(teams_seen) == 2:
            # Cricbuzz shows batting team first, so we need to reconstruct order
            # The team with current batting (has overs) is batting now
            batting_team = None
            for inn in result['innings']:
                if inn['overs'] is not None:
                    batting_team = inn['team']
                    break

            # In a 4-innings match at stumps, the current innings is the 4th
            # Reorder: first team's 1st inn, second team's 1st inn, first team's 2nd, second team's 2nd
            # We need to identify which team batted first overall
            team1 = teams_seen[1] if batting_team == teams_seen[0] else teams_seen[0]
            team2 = teams_seen[0] if batting_team == teams_seen[0] else teams_seen[1]

            # Find innings by team
            team1_innings = [i for i in result['innings'] if i['team'] == team1]
            team2_innings = [i for i in result['innings'] if i['team'] == team2]

            # Sort by runs (lower is likely 1st innings for each team, but not always reliable)
            # Actually, just assign based on standard order
            new_innings = []
            if team1_innings:
                inn = team1_innings[0].copy()
                inn['inning_number'] = 1
                new_innings.append(inn)
            if team2_innings:
                inn = team2_innings[0].copy()
                inn['inning_number'] = 2
                new_innings.append(inn)
            if len(team1_innings) > 1:
                inn = team1_innings[1].copy()
                inn['inning_number'] = 3
                new_innings.append(inn)
            if len(team2_innings) > 1:
                inn = team2_innings[1].copy()
                inn['inning_number'] = 4
                new_innings.append(inn)

            result['innings'] = new_innings

    print(f"    [DEBUG] Cricbuzz parsed: status={result.get('status')}, innings={len(result.get('innings', []))}")
    return result


def scrape_cricket(url):
    """Scrape live cricket data from ESPN Cricinfo."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'sec-ch-ua': '"Google Chrome";v="131"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    next_data = soup.find('script', id='__NEXT_DATA__')

    if not next_data:
        return None

    data = json.loads(next_data.string)

    # Navigate the nested structure safely - may not exist for upcoming matches
    try:
        props = data['props']['appPageProps']['data']['data']
    except (KeyError, TypeError):
        # Match hasn't started yet or different page structure
        return None

    if not props:
        return None

    match = props.get('match', {})
    content = props.get('content', {})

    # Filter out template placeholders from status
    status = match.get('status')
    if status and ('{{' in status or '}}' in status):
        status = None

    result = {
        'status': status,
        'stage': match.get('stage'),
        'state': match.get('state'),
        'teams': [],
        'innings': []
    }

    # Teams and scores
    for team_info in match.get('teams') or []:
        team = team_info.get('team') or {}
        result['teams'].append({
            'name': team.get('longName'),
            'abbreviation': team.get('abbreviation'),
            'score': team_info.get('score'),
            'score_info': team_info.get('scoreInfo'),
            'is_batting': team_info.get('isBatting', False)
        })

    # Innings
    for inn in content.get('innings') or []:
        team = inn.get('team') or {}
        result['innings'].append({
            'team': team.get('longName'),
            'inning_number': inn.get('inningNumber'),
            'runs': inn.get('runs'),
            'wickets': inn.get('wickets'),
            'overs': inn.get('overs')
        })

    return result


def save_to_db(match_id, timestamp, odds_data, cricket_data, errors):
    """Save scraped data to Postgres database."""
    with get_db() as conn:
        with get_cursor(conn) as c:
            # Insert snapshot
            c.execute('''
                INSERT INTO snapshots (match_id, timestamp, match_status, match_stage, match_state, errors)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (
                match_id,
                timestamp,
                cricket_data.get('status') if cricket_data else None,
                cricket_data.get('stage') if cricket_data else None,
                cricket_data.get('state') if cricket_data else None,
                json.dumps(errors) if errors else None
            ))
            snapshot_id = c.fetchone()['id']

            # Insert odds
            if odds_data:
                for outcome, data in odds_data.items():
                    c.execute('''
                        INSERT INTO odds (snapshot_id, outcome, odds, implied_probability)
                        VALUES (%s, %s, %s, %s)
                    ''', (snapshot_id, outcome, data['odds'], data['implied_probability']))

            # Insert innings
            if cricket_data and cricket_data.get('innings'):
                for inn in cricket_data['innings']:
                    c.execute('''
                        INSERT INTO innings (snapshot_id, team, inning_number, runs, wickets, overs)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (snapshot_id, inn['team'], inn['inning_number'], inn['runs'], inn['wickets'], inn['overs']))

        conn.commit()

    return snapshot_id


def get_snapshot_count(match_id=None):
    """Get the total number of snapshots in the database."""
    with get_db() as conn:
        with get_cursor(conn) as c:
            if match_id:
                c.execute('SELECT COUNT(*) as count FROM snapshots WHERE match_id = %s', (match_id,))
            else:
                c.execute('SELECT COUNT(*) as count FROM snapshots')
            count = c.fetchone()['count']
    return count


def scrape_match(match_config):
    """Scrape a single match."""
    # Use event ID directly if available (from events table), otherwise look up by name
    match_id = match_config.get('id') or get_match_id(match_config['name'])

    if not match_id:
        print(f"  Match '{match_config['name']}' not found in database")
        return None

    odds_data = None
    cricket_data = None
    errors = []

    # Scrape odds
    try:
        odds_data = scrape_odds(
            match_config['odds_url'],
            match_config['outcomes'],
            match_config.get('allowed_outcomes')
        )
        if odds_data:
            odds_str = ', '.join([f"{k}: {v['odds']}" for k, v in odds_data.items()])
            print(f"    Odds: {odds_str}")
    except Exception as e:
        errors.append(f"Odds error: {str(e)}")
        print(f"    Odds error: {e}")

    # Scrape cricket - use source based on event config
    score_source = match_config.get('score_source', 'cricinfo')
    try:
        if score_source == 'cricbuzz':
            # Prefer scorecard URL (cleaner parsing), fall back to cricbuzz_url
            scorecard_url = match_config.get('scorecard_url')
            cricbuzz_url = match_config.get('cricbuzz_url')
            if scorecard_url:
                cricket_data = scrape_cricbuzz_scorecard(scorecard_url)
            elif cricbuzz_url:
                cricket_data = scrape_cricbuzz(cricbuzz_url)
        else:
            cricket_url = match_config.get('cricket_url')
            if cricket_url:
                cricket_data = scrape_cricket(cricket_url)

        if cricket_data:
            if cricket_data.get('teams'):
                for t in cricket_data['teams']:
                    if t.get('score'):
                        print(f"    {t['name']}: {t['score']}")
            elif cricket_data.get('innings'):
                # For Cricbuzz, show current innings
                current = [i for i in cricket_data['innings'] if i.get('overs')]
                if current:
                    i = current[-1]
                    print(f"    {i['team']}: {i['runs']}/{i['wickets']} ({i['overs']})")
            print(f"    Status: {cricket_data.get('status')}")
    except Exception as e:
        errors.append(f"Cricket error: {str(e)}")
        print(f"    Cricket error: {e}")

    return match_id, odds_data, cricket_data, errors


def run_once():
    """Run a single scrape cycle. Returns list of results."""
    results = []
    timestamp = datetime.now().isoformat()
    events = get_active_events()
    print(f"[{timestamp}] Scraping {len(events)} active events...")

    if not events:
        print("  No active events to scrape")
        return results

    for match_config in events:
        print(f"\n  {match_config['name']}:")

        result = {
            'event_id': match_config['id'],
            'event_name': match_config['name'],
            'success': False,
            'error': None,
            'snapshot_id': None
        }

        try:
            scrape_result = scrape_match(match_config)
            if scrape_result:
                match_id, odds_data, cricket_data, errors = scrape_result

                # Check for duplicate before saving
                previous = get_last_snapshot(match_id)
                if is_duplicate(odds_data, cricket_data, previous):
                    print(f"    Skipping (no change)")
                    result['success'] = True
                    result['skipped'] = True
                else:
                    snapshot_id = save_to_db(match_id, timestamp, odds_data, cricket_data, errors)
                    total = get_snapshot_count(match_id)
                    print(f"    Saved snapshot #{snapshot_id} (total for match: {total})")
                    result['success'] = True
                    result['snapshot_id'] = snapshot_id
        except Exception as e:
            result['error'] = str(e)
            print(f"    Error: {e}")

        results.append(result)

    return results


def query_latest(match_name=None, as_json=False):
    """Query and display the latest snapshot(s)."""
    with get_db() as conn:
        with get_cursor(conn) as c:
            if match_name:
                c.execute('''
                    SELECT s.*, m.name as match_name, m.match_type
                    FROM snapshots s
                    JOIN matches m ON s.match_id = m.id
                    WHERE m.name = %s
                    ORDER BY s.id DESC LIMIT 1
                ''', (match_name,))
                snapshots = [c.fetchone()]
            else:
                # Get latest for each match
                c.execute('''
                    SELECT s.*, m.name as match_name, m.match_type
                    FROM snapshots s
                    JOIN matches m ON s.match_id = m.id
                    WHERE s.id IN (
                        SELECT MAX(id) FROM snapshots GROUP BY match_id
                    )
                    ORDER BY m.name
                ''')
                snapshots = c.fetchall()

            if not snapshots or not snapshots[0]:
                if as_json:
                    print(json.dumps({"matches": []}))
                else:
                    print("No data yet")
                return

            if as_json:
                output = {"matches": []}
                for snapshot in snapshots:
                    if not snapshot:
                        continue
                    match_data = {
                        "name": snapshot["match_name"],
                        "type": snapshot["match_type"],
                        "timestamp": snapshot["timestamp"],
                        "status": snapshot["match_status"],
                        "stage": snapshot["match_stage"],
                        "state": snapshot["match_state"],
                        "odds": {},
                        "innings": []
                    }
                    # Get odds
                    c.execute('SELECT * FROM odds WHERE snapshot_id = %s', (snapshot['id'],))
                    for o in c.fetchall():
                        match_data["odds"][o["outcome"]] = {
                            "odds": o["odds"],
                            "implied_probability": o["implied_probability"]
                        }
                    # Get innings
                    c.execute('SELECT * FROM innings WHERE snapshot_id = %s', (snapshot['id'],))
                    for inn in c.fetchall():
                        match_data["innings"].append({
                            "team": inn["team"],
                            "inning_number": inn["inning_number"],
                            "runs": inn["runs"],
                            "wickets": inn["wickets"],
                            "overs": inn["overs"]
                        })
                    output["matches"].append(match_data)
                print(json.dumps(output, indent=2))
                return

            for snapshot in snapshots:
                if not snapshot:
                    continue

                print(f"\n{'=' * 60}")
                print(f"Match: {snapshot['match_name']}")
                print(f"Snapshot #{snapshot['id']} - {snapshot['timestamp']}")
                print(f"Status: {snapshot['match_status']} | Stage: {snapshot['match_stage']} | State: {snapshot['match_state']}")

                # Get odds
                c.execute('SELECT * FROM odds WHERE snapshot_id = %s', (snapshot['id'],))
                odds = c.fetchall()
                if odds:
                    print("\nOdds:")
                    for o in odds:
                        print(f"  {o['outcome']}: {o['odds']} ({o['implied_probability']}%)")

                # Get innings
                c.execute('SELECT * FROM innings WHERE snapshot_id = %s', (snapshot['id'],))
                innings = c.fetchall()
                if innings:
                    print("\nInnings:")
                    for inn in innings:
                        print(f"  {inn['inning_number']}) {inn['team']}: {inn['runs']}/{inn['wickets']} ({inn['overs']} ov)")


def cleanup_duplicates(dry_run=True):
    """Remove duplicate snapshots from the database."""
    with get_db() as conn:
        with get_cursor(conn) as c:
            # Get all matches
            c.execute('SELECT id, name FROM events')
            matches = c.fetchall()

            total_deleted = 0

            for match in matches:
                match_id = match['id']
                match_name = match['name']

                # Get all snapshots ordered by id
                c.execute('''
                    SELECT s.id, s.match_status
                    FROM snapshots s
                    WHERE s.match_id = %s
                    ORDER BY s.id
                ''', (match_id,))
                snapshots = c.fetchall()

                if not snapshots:
                    continue

                to_delete = []
                prev = None

                for snap in snapshots:
                    snapshot_id = snap['id']

                    # Get odds
                    c.execute('SELECT outcome, odds FROM odds WHERE snapshot_id = %s', (snapshot_id,))
                    odds = {row['outcome']: row['odds'] for row in c.fetchall()}

                    # Get innings
                    c.execute('SELECT runs, wickets, overs FROM innings WHERE snapshot_id = %s', (snapshot_id,))
                    innings_rows = c.fetchall()

                    cumulative_overs = sum(row['overs'] or 0 for row in innings_rows)
                    total_wickets = sum(row['wickets'] or 0 for row in innings_rows)

                    current = {
                        'id': snapshot_id,
                        'odds': odds,
                        'cumulative_overs': cumulative_overs,
                        'total_wickets': total_wickets,
                        'status': snap['match_status']
                    }

                    if prev:
                        # Check if duplicate
                        if (current['odds'] == prev['odds'] and
                            current['cumulative_overs'] == prev['cumulative_overs'] and
                            current['total_wickets'] == prev['total_wickets'] and
                            current['status'] == prev['status']):
                            to_delete.append(snapshot_id)
                        else:
                            prev = current
                    else:
                        prev = current

                if to_delete:
                    print(f"{match_name}: {len(to_delete)} duplicates found (keeping {len(snapshots) - len(to_delete)})")
                    total_deleted += len(to_delete)

                    if not dry_run:
                        for snapshot_id in to_delete:
                            c.execute('DELETE FROM snapshots WHERE id = %s', (snapshot_id,))
                        conn.commit()
                        print(f"  Deleted {len(to_delete)} snapshots")

            print(f"\nTotal: {total_deleted} duplicates {'would be' if dry_run else ''} deleted")
            if dry_run:
                print("Run with --cleanup --execute to actually delete")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--query', type=str, help='Query latest data for match')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--list', action='store_true', help='List active events')
    parser.add_argument('--cleanup', action='store_true', help='Find and remove duplicate snapshots')
    parser.add_argument('--execute', action='store_true', help='Actually delete (use with --cleanup)')
    args = parser.parse_args()

    if args.cleanup:
        cleanup_duplicates(dry_run=not args.execute)
    elif args.list:
        events = get_active_events()
        print(f"Active events ({len(events)}):")
        for m in events:
            print(f"  - {m['name']} ({m['match_type']})")
    elif args.query:
        query_latest(args.query, as_json=args.json)
    elif args.json:
        query_latest(None, as_json=True)
    else:
        results = run_once()
        print(f"\nScraped {len(results)} events")
        for r in results:
            status = "OK" if r['success'] else f"ERROR: {r['error']}"
            print(f"  {r['event_name']}: {status}")
