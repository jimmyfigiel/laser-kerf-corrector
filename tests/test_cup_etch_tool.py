import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
from PIL import Image

from kerfcorrector import hub


@pytest.fixture
def client():
    app = hub.create_app()
    app.testing = True
    return app.test_client()


def _upload_a_square_png(client) -> str:
    img = Image.fromarray(np.full((200, 200, 4), 255, dtype=np.uint8), mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    resp = client.post(
        "/cup-etcher/api/upload",
        data={"file": (buf, "logo.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    return resp.get_json()["token"]


def test_downloaded_png_embeds_a_dpi_matching_the_request():
    # Regression test: the downloaded PNG used to carry no DPI/resolution
    # metadata at all (Image.save with no dpi= argument), so any DPI-aware
    # software (image editors, laser/engraving software) had nothing to read
    # and would show the wrong physical size until the user manually told it
    # what resolution to assume. The file must now be immediately usable
    # with no manual resizing -- its own DPI tag must match generate()'s
    # reported effective_dpi (and, when the resolution cap doesn't kick in,
    # the originally requested dpi too).
    app = hub.create_app()
    app.testing = True
    client = app.test_client()
    token = _upload_a_square_png(client)

    resp = client.post(
        "/cup-etcher/api/generate",
        json={
            "token": token,
            "bottom_circumference_mm": 207,
            "top_circumference_mm": 285,
            "side_length_mm": 148,
            "top_offset_mm": 0,
            "design_width_mm": 60,
            "dpi": 150,
            "dither": False,
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["effective_dpi"] == pytest.approx(150)

    download = client.get("/cup-etcher/api/download/" + data["download_token"])
    assert download.status_code == 200
    out_img = Image.open(io.BytesIO(download.data))
    assert out_img.info.get("dpi") == pytest.approx((150, 150), abs=0.5)


def test_effective_dpi_matches_file_even_when_resolution_is_capped():
    # A design large enough (huge design width) to trip cup_etch.MAX_OUTPUT_PX
    # at the requested DPI must still produce a file whose own DPI tag is
    # consistent with its actual pixel dimensions -- not the originally
    # requested (and silently un-honored) DPI, which would misreport the
    # file's true physical size.
    app = hub.create_app()
    app.testing = True
    client = app.test_client()
    token = _upload_a_square_png(client)

    resp = client.post(
        "/cup-etcher/api/generate",
        json={
            "token": token,
            "bottom_circumference_mm": 2000,
            "top_circumference_mm": 2000,
            "side_length_mm": 148,
            "top_offset_mm": 0,
            "design_width_mm": 90,
            "dpi": 1200,
            "dither": False,
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["effective_dpi"] < 1200  # the cap forced a lower effective resolution

    download = client.get("/cup-etcher/api/download/" + data["download_token"])
    out_img = Image.open(io.BytesIO(download.data))
    assert out_img.info["dpi"][0] == pytest.approx(data["effective_dpi"], abs=0.5)
    # And the file's physical size (px / its own dpi) must match the
    # geometry's reported physical width, regardless of the cap.
    physical_w_in = out_img.width / out_img.info["dpi"][0]
    assert physical_w_in * 25.4 == pytest.approx(data["output_width_mm"], abs=0.5)
