import requests
from bs4 import BeautifulSoup
import json
import re

def scrape_espn_cricket(match_url):
    """
    Scrape live cricket data from ESPN Cricinfo.

    Args:
        match_url: Full URL to the ESPN Cricinfo match page (live-cricket-score or ball-by-ball-commentary)

    Returns:
        dict with match info, innings, and recent commentary
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'sec-ch-ua': '"Google Chrome";v="131"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
    }

    response = requests.get(match_url, headers=headers)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    next_data = soup.find('script', id='__NEXT_DATA__')

    if not next_data:
        raise ValueError("Could not find match data on page")

    data = json.loads(next_data.string)
    props = data['props']['appPageProps']['data']['data']
    match = props.get('match', {})
    content = props.get('content', {})

    result = {
        'status': match.get('status'),
        'stage': match.get('stage'),
        'state': match.get('state'),
        'teams': [],
        'innings': [],
        'live_summary': {},
        'recent_commentary': []
    }

    # Teams and scores
    for team_info in match.get('teams', []):
        team = team_info.get('team', {})
        result['teams'].append({
            'name': team.get('longName'),
            'abbreviation': team.get('abbreviation'),
            'score': team_info.get('score'),
            'score_info': team_info.get('scoreInfo'),
            'is_batting': team_info.get('isBatting', False)
        })

    # Innings details
    for inn in content.get('innings', []):
        result['innings'].append({
            'team': inn.get('team', {}).get('longName'),
            'inning_number': inn.get('inningNumber'),
            'runs': inn.get('runs'),
            'wickets': inn.get('wickets'),
            'overs': inn.get('overs'),
            'run_rate': inn.get('runRate'),
            'target': inn.get('target')
        })

    # Live summary (current batters/bowlers)
    live_summary = content.get('supportInfo', {}).get('liveSummary', {})
    if live_summary:
        result['live_summary'] = {
            'current_batters': [
                {
                    'name': b.get('player', {}).get('longName'),
                    'runs': b.get('runs'),
                    'balls': b.get('balls'),
                    'fours': b.get('fours'),
                    'sixes': b.get('sixes'),
                    'strike_rate': b.get('strikerate')
                }
                for b in live_summary.get('currentBatters', [])
            ],
            'current_bowlers': [
                {
                    'name': b.get('player', {}).get('longName'),
                    'overs': b.get('overs'),
                    'maidens': b.get('maidens'),
                    'runs': b.get('conceded'),
                    'wickets': b.get('wickets'),
                    'economy': b.get('economy')
                }
                for b in live_summary.get('currentBowlers', [])
            ]
        }

    # Recent ball-by-ball commentary
    for comm in content.get('recentBallCommentary', {}).get('ballComments', [])[:20]:
        text_items = comm.get('commentTextItems', [])
        text = text_items[0].get('html', '') if text_items else ''
        text = re.sub('<[^>]+>', '', text)  # Strip HTML tags

        result['recent_commentary'].append({
            'over': comm.get('oversActual'),
            'title': comm.get('title'),
            'text': text,
            'is_wicket': comm.get('isWicket', False),
            'is_boundary': comm.get('isFour', False) or comm.get('isSix', False),
            'runs': comm.get('totalRuns', 0)
        })

    return result


def print_match_summary(data):
    """Print a formatted match summary."""
    print("=" * 60)
    print(f"STATUS: {data['status']} | STAGE: {data['stage']} | STATE: {data['state']}")
    print("=" * 60)

    print("\nTEAMS:")
    for team in data['teams']:
        batting = " (batting)" if team['is_batting'] else ""
        print(f"  {team['name']}: {team['score']} - {team['score_info']}{batting}")

    if data['innings']:
        print("\nINNINGS:")
        for inn in data['innings']:
            print(f"  {inn['inning_number']}) {inn['team']}: {inn['runs']}/{inn['wickets']} ({inn['overs']} ov) RR: {inn['run_rate']}")

    if data['live_summary'].get('current_batters'):
        print("\nCURRENT BATTERS:")
        for b in data['live_summary']['current_batters']:
            print(f"  {b['name']}: {b['runs']} ({b['balls']}) SR: {b['strike_rate']}")

    if data['live_summary'].get('current_bowlers'):
        print("\nCURRENT BOWLERS:")
        for b in data['live_summary']['current_bowlers']:
            print(f"  {b['name']}: {b['overs']}-{b['maidens']}-{b['runs']}-{b['wickets']} Econ: {b['economy']}")

    print("\nRECENT COMMENTARY:")
    for comm in data['recent_commentary'][:5]:
        marker = ""
        if comm['is_wicket']:
            marker = " [WICKET]"
        elif comm['is_boundary']:
            marker = f" [{comm['runs']}]"
        print(f"  {comm['over']} {comm['title']}{marker}")
        if comm['text']:
            print(f"       {comm['text'][:80]}...")


if __name__ == "__main__":
    # Ashes 3rd Test
    url = "https://www.espncricinfo.com/series/the-ashes-2025-26-1455609/australia-vs-england-3rd-test-1455613/live-cricket-score"

    try:
        data = scrape_espn_cricket(url)
        print_match_summary(data)

        # Also available as JSON
        # print(json.dumps(data, indent=2))
    except Exception as e:
        print(f"Error: {e}")
