def rank_videos(signals, ranking_model):
    return sorted(signals, key=lambda v: ranking_model.calculate_rank(v['signals']), reverse=True)