#!/usr/bin/env python3
"""
Classify trades into market categories (politics, crypto, sports, world_events, other)
based on market title keywords. Output enriched trades with category field.
"""

import json
from pathlib import Path


def classify_market(title: str) -> str:
    """Classify market into category based on title keywords."""
    title_lower = title.lower()

    # Politics keywords
    if any(word in title_lower for word in [
        'election', 'vote', 'congress', 'senate', 'house', 'president',
        'governor', 'mayor', 'party', 'democrat', 'republican', 'trump',
        'biden', 'harris', 'politician', 'presidential', 'campaign', 'political',
        'ballot', 'referendum', 'parliament', 'parliament', 'minister', 'minister'
    ]):
        return 'politics'

    # Crypto keywords
    if any(word in title_lower for word in [
        'bitcoin', 'ethereum', 'crypto', 'btc', 'eth', 'blockchain',
        'nft', 'defi', 'token', 'altcoin', 'solana', 'polygon', 'cardano',
        'ripple', 'doge', 'meme coin', 'web3'
    ]):
        return 'crypto'

    # Sports keywords
    if any(word in title_lower for word in [
        'nba', 'nfl', 'nhl', 'mlb', 'soccer', 'football', 'basketball',
        'baseball', 'hockey', 'championship', 'super bowl', 'world series',
        'stanley cup', 'olympics', 'sports', 'team', 'player', 'nba', 'nfl',
        'ncaa', 'premier league', 'champions league'
    ]):
        return 'sports'

    # World events keywords (weather, economics, conflicts, natural disasters)
    if any(word in title_lower for word in [
        'weather', 'temperature', 'hurricane', 'earthquake', 'tsunami', 'volcano',
        'tornado', 'flood', 'snow', 'rain', 'storm', 'climate', 'war', 'conflict',
        'peace', 'recession', 'economy', 'gdp', 'inflation', 'unemployment',
        'stock', 'market', 's&p', 'dow', 'nasdaq', 'death toll'
    ]):
        return 'world_events'

    return 'other'


def main():
    workspace = Path('/home/clawd/claude/openclaw-orchestration/projects/find-10-highly-profitable-polymarket')
    input_file = workspace / 'trades_raw.jsonl'
    output_file = workspace / 'categorized_trades.jsonl'

    trade_count = 0

    with open(input_file, 'r') as infile, open(output_file, 'w') as outfile:
        for line in infile:
            record = json.loads(line)

            # Process trades array
            if 'trades' in record:
                for trade in record['trades']:
                    # Add category if not present, re-classify if missing
                    trade['category'] = classify_market(trade.get('title', ''))

                    # Write enriched trade record (one per line)
                    outfile.write(json.dumps(trade) + '\n')
                    trade_count += 1

    print(f"✓ Classified {trade_count:,} trades into categories")
    print(f"✓ Output: {output_file}")

    # Quick validation
    with open(output_file, 'r') as f:
        sample = json.loads(f.readline())
        print(f"✓ Sample record has category field: {sample.get('category', 'MISSING')}")


if __name__ == '__main__':
    main()
