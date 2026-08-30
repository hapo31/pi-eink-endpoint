import sys
from pathlib import Path

from fastapi import FastAPI
from PIL import Image, ImageDraw

WAVESHARE_LIB = (
    Path(__file__).parent / "waveshare_e_paper/RaspberryPi_JetsonNano/python/lib"
)
sys.path.insert(0, str(WAVESHARE_LIB))

from waveshare_epd import epd2in9_V3

app = FastAPI()


@app.post("/eink")
async def update_eink(data: dict):
    epd = epd2in9_V3.EPD()
    epd.init()

    image = Image.new("1", (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), data.get("text", "Hello E-ink"), fill=0)

    epd.display(epd.getbuffer(image))
    epd.sleep()
    return {"message": "E-ink display updated", "data": data}
