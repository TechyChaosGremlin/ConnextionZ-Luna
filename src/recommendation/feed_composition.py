def compose_feed(candidate_pools):
    personalized_content = candidate_pools['fresh_uploads'] + candidate_pools['interest_matches']
    related_content = candidate_pools['similar_creators'] + candidate_pools['followed_creators_network'] + candidate_pools['trending_interests']
    exploration_content = candidate_pools['collab_discovery'] + candidate_pools['exploration']

    feed = [
        *personalized_content[:70],
        *related_content[:20],
        *exploration_content[:10]
    ]
    return feed