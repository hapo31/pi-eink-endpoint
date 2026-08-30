import io
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from PIL import Image, ImageDraw

WAVESHARE_LIB = (
    Path(__file__).parent / "waveshare_e_paper/RaspberryPi_JetsonNano/python/lib"
)
sys.path.insert(0, str(WAVESHARE_LIB))

from waveshare_epd import epd2in9_V3


class EinkHandler(BaseHTTPRequestHandler):
    lock = Lock()

    def do_POST(self):
        if self.path == "/text":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data)
            except json.JSONDecodeError:
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.end_headers()
                self.wfile.write(b"Invalid JSON")
                return

            with self.lock:
                response = update_eink_from_text(data)

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        elif self.path == "/image":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)

            with self.lock:
                response = update_eink_from_image(post_data)

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            self.wfile.write(b"Not Found")


def update_eink_from_text(data: dict):
    epd = epd2in9_V3.EPD()
    epd.init()

    image = Image.new("1", (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), data.get("text", "Hello E-ink"), fill=0)

    epd.display(epd.getbuffer(image))
    epd.sleep()
    return {"message": "E-ink display updated", "data": data}


def update_eink_from_image(binary_image: bytes):
    epd = epd2in9_V3.EPD()
    epd.init()

    image = Image.open(io.BytesIO(binary_image)).convert("L")
    image = image.resize((epd.width, epd.height))

    image = image.point(lambda value: (0x00, 0x80, 0xC0, 0xFF)[value * 4 // 256])

    epd.Init_4Gray()
    epd.display_4Gray(epd.getbuffer_4Gray(image))
    epd.sleep()

    return {"message": "E-ink display updated from image"}


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8000), EinkHandler)
    print("Listening on http://0.0.0.0:8000")
    server.serve_forever()
