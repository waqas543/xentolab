from data_platform.sources.commoncrawl.index import Block, block_containing, domain_range


def test_domain_range_covers_domain_and_subdomains_only():
    lower, upper = domain_range("example.com")
    inside = ["com,example)/", "com,example)/about", "com,example,www)/", "com,example,shop)/x"]
    outside = ["com,examples)/", "com,example-shop)/", "com,exampla)/", "org,example)/"]
    assert all(lower <= key < upper for key in inside)
    assert not any(lower <= key < upper for key in outside)


def test_block_containing_picks_the_last_block_starting_at_or_before_the_key():
    blocks = [Block("com,a)/", "f", 0, 1, 1), Block("com,example)/blog", "f", 1, 1, 2), Block("com,example,www)/", "f", 2, 1, 3)]
    assert block_containing(blocks, "com,example)/").key == "com,a)/"            # homepage sorts before /blog
    assert block_containing(blocks, "com,example,www)/").key == "com,example,www)/"
    assert block_containing(blocks, "com,0)/") is None
