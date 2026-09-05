import io
import json
import logging
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Thread

from PIL import Image, ImageDraw, ImageOps

WAVESHARE_LIB = (
    Path(__file__).parent / "waveshare_e_paper/RaspberryPi_JetsonNano/python/lib"
)
sys.path.insert(0, str(WAVESHARE_LIB))

from waveshare_epd import epd2in9_V3


logger = logging.getLogger(__name__)


class EinkServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class=None):
        super().__init__(server_address, handler_class or EinkHandler)
        self.render_queue = Queue()
        self.render_worker = Thread(target=self._render_jobs, daemon=True)
        self.render_worker.start()

    def _render_jobs(self):
        while True:
            job = self.render_queue.get()
            try:
                if job is None:
                    return
                render, payload = job
                render(payload)
            except Exception:
                logger.exception("E-ink rendering failed")
            finally:
                self.render_queue.task_done()

    def server_close(self):
        super().server_close()
        self.render_queue.put(None)
        self.render_worker.join()


class EinkHandler(BaseHTTPRequestHandler):
    def enqueue_render(self, render, payload):
        self.server.render_queue.put((render, payload))
        response = json.dumps({"message": "E-ink update queued"}).encode()
        self.send_response(HTTPStatus.ACCEPTED)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

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

            self.enqueue_render(update_eink_from_text, data)

        elif self.path == "/image":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)

            self.enqueue_render(update_eink_from_image, post_data)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            self.wfile.write(b"Not Found")


def update_eink_from_text(data: dict):
    epd = epd2in9_V3.EPD()
    epd.init()

    # The driver rotates landscape images 90 degrees into panel coordinates.
    image = Image.new("1", (epd.height, epd.width), 255)
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), data.get("text", "Hello E-ink"), fill=0)

    epd.display(epd.getbuffer(image))
    epd.sleep()
    return {"message": "E-ink display updated", "data": data}


def update_eink_from_image(binary_image: bytes):
    epd = epd2in9_V3.EPD()
    epd.init()

    image = Image.open(io.BytesIO(binary_image)).convert("L")
    # The driver rotates landscape images 90 degrees into panel coordinates.
    display_size = (epd.height, epd.width)
    fitted = ImageOps.contain(image, display_size, Image.Resampling.LANCZOS)
    image = Image.new("L", display_size, 255)
    image.paste(
        fitted,
        ((image.width - fitted.width) // 2, (image.height - fitted.height) // 2),
    )

    image = image.point(lambda value: (0x00, 0x80, 0xC0, 0xFF)[value * 4 // 256])

    epd.Init_4Gray()
    epd.display_4Gray(epd.getbuffer_4Gray(image))
    epd.sleep()

    return {"message": "E-ink display updated from image"}


if __name__ == "__main__":
    with EinkServer(("0.0.0.0", 8000)) as server:
        print("Listening on http://0.0.0.0:8000")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
