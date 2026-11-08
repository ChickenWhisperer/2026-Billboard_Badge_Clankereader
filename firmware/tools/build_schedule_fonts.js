#!/usr/bin/env node
'use strict';

// Host-only generator. Dependencies and the source TTF are supplied explicitly.
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const [converterArg, sourceArg, outputArg] = process.argv.slice(2);
if (!converterArg || !sourceArg || process.argv.length > 5) {
  console.error('Usage: node build_schedule_fonts.js <lv_font_conv package directory> <Montserrat-Medium.ttf> [output directory]');
  process.exit(1);
}

const converter = path.resolve(converterArg);
const source = path.resolve(sourceArg);
const output = outputArg ? path.resolve(outputArg) : path.resolve(__dirname, '../badge/data/fonts');
const sha256 = data => crypto.createHash('sha256').update(data).digest('hex');
const sourceHash = '421f26b23e2be6b98373d32acd3cb2897b154d4bf0a77d26534ce476e4cbed53';
const profiles = [
  { size: 14, lineHeight: 16, baseline: 3, name: 'montserrat-14-soft.bin',
    hash: '6082b07dbe2d5a86261e4d96c28280c6ebba9d11e0162cd1f70e805615463b20' },
  { size: 16, lineHeight: 18, baseline: 3, name: 'montserrat-16-strong.bin',
    hash: 'b4a9368e4dda06a630e13ca1c43545676a66f16d633de1bec243e04ddb5035f0' },
];

if (JSON.parse(fs.readFileSync(path.join(converter, 'package.json'))).version !== '1.5.3') {
  throw new Error('This generator requires lv_font_conv 1.5.3');
}
if (sha256(fs.readFileSync(source)) !== sourceHash) {
  throw new Error('Source must be the verified LVGL v9.2.2 Montserrat-Medium.ttf');
}

const Font = require(path.join(converter, 'lib/font/font'));
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'badge-fonts-'));
try {
  const generated = [];
  for (const profile of profiles) {
    const dump = path.join(temporary, String(profile.size));
    execFileSync(process.execPath, [path.join(converter, 'lv_font_conv.js'),
      '--font', source, '--autohint-strong', '--size', String(profile.size),
      '--bpp', '4', '--range', '0x20-0x7F,0xB0,0x2022',
      '--no-compress', '--no-prefilter', '--force-fast-kern-format',
      '--format', 'dump', '--full-info', '-o', dump], { stdio: 'inherit' });
    const data = JSON.parse(fs.readFileSync(path.join(dump, 'font_info.json')));
    if (profile.size === 14) {
      // Expand faint edges: only coverage 3/15 becomes 4/15. With gray rendering,
      // these pixels become gray; black cores and grid-fitted geometry stay put.
      for (const glyph of data.glyphs) {
        for (const row of glyph.pixels) {
          for (let x = 0; x < row.length; x++) {
            if ((row[x] >>> 4) === 3) row[x] = 4 << 4;
          }
        }
      }
    }
    // The ASCII subset omits FontAwesome symbols that enlarged built-in metrics.
    data.ascent = profile.lineHeight - profile.baseline;
    data.descent = -profile.baseline;
    const binary = new Font(data, {
      bpp: 4, no_compress: true, no_prefilter: true, fast_kerning: true,
    }).toBin();
    if (sha256(binary) !== profile.hash) {
      throw new Error('Generated font does not match the expected hash: ' + profile.name);
    }
    generated.push([profile, binary]);
  }
  fs.mkdirSync(output, { recursive: true });
  for (const [profile, binary] of generated) {
    fs.writeFileSync(path.join(output, profile.name), binary);
    console.log(profile.name + ' ' + binary.length + ' bytes ' + profile.hash);
  }
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
