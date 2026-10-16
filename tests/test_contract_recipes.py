"""Recipes over a real socket, against tests/stub_cozi.py (exact paths, apikey, auth)."""

import email
import email.policy
import json

from aiohttp import web

from cozi_client.recipes import CoziRecipe

RECIPES = "/2103/{acct}/food/recipe/"
PHOTO = "/2103/{acct}/food/recipe/rb1/photo"
# A valid 1x1 transparent PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c6360000200000500017a5eab3f0000000049454e44ae426082"
)
STORED = {
    "recipeBoxId": "rb1",
    "recipeId": "r1",
    "name": "Soup",
    "ingredients": [{"ingredientId": "i1", "name": "water"}],
    "instructions": "boil",
    "photos": [],
}


async def echo(request: web.Request) -> web.Response:
    return web.json_response(await request.json())


def multipart_parts(recorded):
    """Decode a recorded multipart body with the stdlib (the stub already read it)."""
    head = f"Content-Type: {recorded['headers']['Content-Type']}\r\n\r\n".encode()
    message = email.message_from_bytes(head + recorded["body"], policy=email.policy.HTTP)
    return list(message.iter_parts())


async def test_recipe_flow_uses_exact_paths(stub):
    stub.route("GET", RECIPES, json=[STORED])
    stub.route("POST", RECIPES, echo)
    stub.route("DELETE", "/2103/{acct}/food/recipe/rb1", status=204)
    stub.route("GET", "/2103/{acct}/food/curated/recipe/", json=[{"recipeId": "c1", "name": "Pie"}])
    stub.route("POST", "/2103/{acct}/food/scrape/recipe/", json={"name": "Pancakes", "ingredients": [{"name": "egg"}]})
    async with stub.client() as cozi:
        (recipe,) = await cozi.get_recipes()
        recipe.notes = "n"
        assert (await cozi.update_recipe(recipe)).notes == "n"
        created = await cozi.create_recipe("Soup", ["water"])
        assert created.name == "Soup"
        await cozi.delete_recipe("rb1")
        assert (await cozi.get_curated_recipes())[0].recipe_id == "c1"
        assert (await cozi.import_recipe("https://example.com/p")).ingredients[0].name == "egg"
    resource = [r for r in stub.requests if "/food/" in r["path"]]
    assert len(resource) == 7  # update_recipe re-reads the box to confirm the recipe exists
    assert len(stub.sent("GET", RECIPES)) == 2
    assert all(r["headers"]["Authorization"] == "Bearer tok-1" for r in resource)
    assert stub.sent("POST", RECIPES)[0]["json"] == {**STORED, "notes": "n", "title": "Soup"}


async def test_photo_upload_is_multipart_file_with_query_token(stub):
    stub.route("GET", RECIPES, json=[STORED])
    stub.route("POST", PHOTO, json={"thumbUrl": "t", "viewUrl": "v"}, auth="query")
    stub.route("POST", RECIPES, echo)
    async with stub.client() as cozi:
        saved = await cozi.upload_recipe_photo(CoziRecipe.model_validate(STORED), PNG, "pixel.png")
    assert saved.photos == [{"thumbUrl": "t", "viewUrl": "v"}]
    (upload,) = stub.sent("POST", PHOTO)
    assert upload["query"]["auth"] == "tok-1"
    assert "Authorization" not in upload["headers"]
    (part,) = multipart_parts(upload)
    assert part.get_param("name", header="content-disposition") == "file"
    assert part.get_filename() == "pixel.png"
    assert part.get_content_type() == "image/png"
    assert part.get_payload(decode=True) == PNG
    assert stub.sent("POST", RECIPES)[0]["json"]["photos"] == [{"thumbUrl": "t", "viewUrl": "v"}]


async def test_photo_upload_401_replays_with_new_token_and_fresh_form(stub):
    async def box_then_rotate(request: web.Request) -> web.Response:
        # The existence check passes; the token then rotates, so the upload is the
        # request that meets the 401.
        if len(stub.sent("GET", RECIPES)) == 1:
            stub.token = "rotated-elsewhere"
        return web.json_response([STORED])

    stub.route("GET", RECIPES, box_then_rotate)
    stub.route("POST", PHOTO, json={"thumbUrl": "t"}, auth="query")
    stub.route("POST", RECIPES, echo)
    async with stub.client() as cozi:
        await cozi.authenticate()
        await cozi.upload_recipe_photo(CoziRecipe.model_validate(STORED), PNG, "pixel.png")
    first, second = stub.sent("POST", PHOTO)
    assert (first["query"]["auth"], second["query"]["auth"]) == ("tok-1", "tok-2")
    assert multipart_parts(second)[0].get_payload(decode=True) == PNG
    assert json.loads(stub.sent("POST", RECIPES)[0]["body"])["photos"] == [{"thumbUrl": "t"}]
