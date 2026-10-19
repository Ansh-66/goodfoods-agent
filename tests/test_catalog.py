from goodfoods import catalog

from .conftest import FRIDAY, error_code


def test_filters_by_cuisine_and_ranks_by_distance(conn):
    found = catalog.recommend_restaurants(conn, area="Koramangala 5th Block", cuisine="south indian", limit=5)
    assert found["count"] >= 2
    assert all("South Indian" in r["cuisines"] for r in found["restaurants"])
    assert all("km from Koramangala" in r["reason"] for r in found["restaurants"])


def test_understands_guest_wording(conn):
    biryani = catalog.recommend_restaurants(conn, cuisine="biryani", limit=5)["restaurants"]
    assert biryani and all({"Mughlai", "Andhra"} & set(r["cuisines"]) for r in biryani)
    veg = catalog.recommend_restaurants(conn, cuisine="pure veg", features=["outdoor"], limit=5)["restaurants"]
    for r in veg:
        features = catalog.get_restaurant_details(conn, r["restaurant_id"])["features"]
        assert {"veg_friendly", "outdoor_seating"} <= set(features)


def test_unknown_values_return_the_valid_ones(conn):
    assert error_code(catalog.recommend_restaurants, conn, cuisine="Ethiopian") == "no_match"
    assert error_code(catalog.recommend_restaurants, conn, area="Mumbai") == "unknown_area"
    assert error_code(catalog.recommend_restaurants, conn, features=["helipad"]) == "unknown_feature"


def test_free_outlets_rank_first(conn):
    found = catalog.recommend_restaurants(
        conn, area="Koramangala", cuisine="Vegetarian", party_size=4, date_str=FRIDAY, time_str="20:00", limit=5
    )
    flags = [r["available"] for r in found["restaurants"]]
    assert flags == sorted(flags, reverse=True)
    assert "R001" not in [r["restaurant_id"] for r in found["restaurants"] if r["available"]]


def test_a_named_full_outlet_is_reported_with_its_free_times(conn):
    found = catalog.recommend_restaurants(
        conn, area="Whitefield", name="Wok Street", party_size=2, date_str=FRIDAY, time_str="20:00"
    )["restaurants"]
    first = found[0]
    assert (first["restaurant_id"], first["available"]) == ("R004", False)
    assert "18:30" in first["other_times"] and "free instead at" in first["reason"]
