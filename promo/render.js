// node render.js stills 4.5 6.2 ...   -> still_<t>.png
// node render.js video out.mp4        -> 30 fps H.264
const puppeteer = require('puppeteer-core');
const ffmpeg = require('ffmpeg-static');
const { spawn } = require('child_process');
const path = require('path');

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const FPS = 30;

(async () => {
  const [mode, ...args] = process.argv.slice(2);
  const layout = process.env.LAYOUT || 'full', dsf = parseFloat(process.env.DSF || '1');
  const browser = await puppeteer.launch({ executablePath: EDGE, headless: true,
    args: ['--allow-file-access-from-files', '--hide-scrollbars'] });
  const page = await browser.newPage();
  const url = 'file:///' + path.join(__dirname, 'promo.html').replace(/\\/g, '/') + '?layout=' + layout;
  await page.goto(url, { waitUntil: 'networkidle0' });
  const size = await page.evaluate(() => SIZE);
  await page.setViewport({ width: size.w, height: size.h, deviceScaleFactor: dsf });
  await page.goto(url, { waitUntil: 'networkidle0' });
  await page.evaluate(() => window.ready);

  if (mode === 'stills') {
    for (const t of args) {
      await page.evaluate(t => render(t), parseFloat(t));
      await page.screenshot({ path: path.join(__dirname, `still_${layout}_${t}.png`) });
    }
  } else {
    const out = path.join(__dirname, args[0] || 'promo.mp4');
    const dur = await page.evaluate(() => DURATION);
    const enc = spawn(ffmpeg, ['-y', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'slow', '-movflags', '+faststart', out],
      { stdio: ['pipe', 'ignore', 'inherit'] });
    const n = Math.round(dur * FPS);
    for (let i = 0; i <= n; i++) {
      await page.evaluate(t => render(t), i / FPS);
      const buf = await page.screenshot({ type: 'png' });
      if (!enc.stdin.write(buf)) await new Promise(r => enc.stdin.once('drain', r));
    }
    enc.stdin.end();
    await new Promise(r => enc.on('close', r));
    console.log('wrote', out, n + 1, 'frames');
  }
  await browser.close();
})();
