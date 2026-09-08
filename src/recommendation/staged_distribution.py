def measure_performance(videos):
    """Return whether the staged sample contains videos to evaluate."""
    return bool(videos)


def expand(videos):
    """Return the videos selected for the next distribution stage."""
    return videos


def distribute_videos(videos):
    test_videos = videos[:10]
    successful = measure_performance(test_videos)
    if successful:
        expand(videos[10:])
        return True
    return False