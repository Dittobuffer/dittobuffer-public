"""Render original, explicitly virtual artwork; contains no place photographs."""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
def main():
    image=Image.new('RGB',(640,640),'#e7f4f1');draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((28,28,612,612),radius=38,fill='#ffffff',outline='#316c74',width=4)
    draw.ellipse((130,94,510,474),fill='#d1efe8',outline='#316c74',width=6)
    for left,right in [(176,464),(238,402),(296,344)]:draw.ellipse((left,94,right,474),outline='#73a9a5',width=3)
    for top,bottom in [(163,405),(217,351),(264,304)]:draw.ellipse((130,top,510,bottom),outline='#73a9a5',width=3)
    title=ImageFont.load_default(size=30);caption=ImageFont.load_default(size=18)
    draw.rectangle((78,488,562,556),fill='#316c74')
    draw.text((320,522),'VIRTUAL POSTCARD',font=title,fill='white',anchor='mm')
    draw.text((320,584),'Original explorer illustration - not a POI photo',font=caption,fill='#316c74',anchor='mm')
    image.save(ROOT/'src/dittobuffer/resources/world-postcard.png',optimize=True)
    print('Rendered original virtual Postcard illustration.')
if __name__=='__main__':main()
