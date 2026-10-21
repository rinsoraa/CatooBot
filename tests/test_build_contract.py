"""R4 build request parsing tests: deterministic, no world actions."""

from app.tasks.build_plan import BuildCell, BuildRequest, parse_build_request


def test_single_block_request_parses_explicit_coordinates_and_item() -> None:
    request = parse_build_request("在 (12035, 100, 16032) 放一块工作台")
    assert request is not None
    assert request.problem == ""
    assert request.item == "minecraft:crafting_table"
    assert request.cells == (BuildCell(12035, 100, 16032, "up"),)


def test_axis_aligned_range_expands_to_at_most_four_cells() -> None:
    request = parse_build_request("在 (12035, 100, 16032) 到 (12037, 100, 16032) 放三块圆石")
    assert request is not None and not request.problem
    assert request.item == "minecraft:cobblestone"
    assert [cell.x for cell in request.cells] == [12035, 12036, 12037]


def test_diagonal_range_is_refused() -> None:
    request = parse_build_request("在 (1, 2, 3) 到 (2, 3, 4) 放一块泥土")
    assert request is not None
    assert request.problem == "region_not_axis_aligned"


def test_over_four_cells_is_refused() -> None:
    request = parse_build_request("在 (1, 2, 3) 到 (6, 2, 3) 放六块泥土")
    assert request is not None
    assert request.problem == "too_many_cells"


def test_unknown_item_is_refused() -> None:
    request = parse_build_request("在 (1, 2, 3) 放一块钻石块")
    assert request is not None
    assert request.problem == "unsupported_item"


def test_non_build_text_is_not_claimed() -> None:
    assert parse_build_request("帮我采一块木头并捡回来") is None
    assert parse_build_request("今天天气不错") is None


def test_request_region_is_bounding_box() -> None:
    request = BuildRequest(
        item="minecraft:dirt",
        cells=(BuildCell(1, 2, 3), BuildCell(3, 2, 3)),
    )
    low, high = request.region()
    assert low == {"x": 1, "y": 2, "z": 3}
    assert high == {"x": 3, "y": 2, "z": 3}


def test_explicit_range_count_mismatch_is_refused() -> None:
    request = parse_build_request("在 (1, 2, 3) 到 (3, 2, 3) 放一块泥土")
    assert request is not None
    assert request.problem == "count_mismatch"
