/**
 * Generate SVG path data from world-atlas TopoJSON.
 * Outputs a JSON file mapping ISO 3166-1 alpha-2 codes to SVG path strings.
 * Uses Natural Earth equirectangular projection scaled to a 1000x500 viewBox.
 */
import { readFileSync, writeFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Dynamic imports for ESM compatibility
const topoData = JSON.parse(
  readFileSync(join(__dirname, '../node_modules/world-atlas/countries-110m.json'), 'utf-8')
);

// topojson-client
import { feature } from 'topojson-client';

// ISO 3166-1 numeric → alpha-2 mapping (comprehensive)
const numToAlpha2 = {
  '004': 'AF', '008': 'AL', '012': 'DZ', '024': 'AO', '032': 'AR',
  '036': 'AU', '040': 'AT', '050': 'BD', '056': 'BE', '064': 'BT',
  '068': 'BO', '070': 'BA', '072': 'BW', '076': 'BR', '084': 'BZ',
  '096': 'BN', '100': 'BG', '104': 'MM', '108': 'BI', '112': 'BY',
  '116': 'KH', '120': 'CM', '124': 'CA', '140': 'CF', '144': 'LK',
  '148': 'TD', '152': 'CL', '156': 'CN', '158': 'TW', '170': 'CO',
  '178': 'CG', '180': 'CD', '188': 'CR', '191': 'HR', '192': 'CU',
  '196': 'CY', '203': 'CZ', '204': 'BJ', '208': 'DK', '214': 'DO',
  '218': 'EC', '222': 'SV', '226': 'GQ', '231': 'ET', '232': 'ER',
  '233': 'EE', '242': 'FJ', '246': 'FI', '250': 'FR', '260': 'TF',
  '262': 'DJ', '266': 'GA', '268': 'GE', '270': 'GM', '275': 'PS',
  '276': 'DE', '288': 'GH', '300': 'GR', '308': 'GD', '320': 'GT',
  '324': 'GN', '328': 'GY', '332': 'HT', '340': 'HN', '348': 'HU',
  '352': 'IS', '356': 'IN', '360': 'ID', '364': 'IR', '368': 'IQ',
  '372': 'IE', '376': 'IL', '380': 'IT', '384': 'CI', '388': 'JM',
  '392': 'JP', '398': 'KZ', '400': 'JO', '404': 'KE', '408': 'KP',
  '410': 'KR', '414': 'KW', '417': 'KG', '418': 'LA', '422': 'LB',
  '426': 'LS', '428': 'LV', '430': 'LR', '434': 'LY', '440': 'LT',
  '442': 'LU', '450': 'MG', '454': 'MW', '458': 'MY', '466': 'ML',
  '478': 'MR', '480': 'MU', '484': 'MX', '496': 'MN', '498': 'MD',
  '504': 'MA', '508': 'MZ', '512': 'OM', '516': 'NA', '524': 'NP',
  '528': 'NL', '540': 'NC', '548': 'VU', '554': 'NZ', '558': 'NI',
  '562': 'NE', '566': 'NG', '578': 'NO', '586': 'PK', '591': 'PA',
  '598': 'PG', '600': 'PY', '604': 'PE', '608': 'PH', '616': 'PL',
  '620': 'PT', '624': 'GW', '626': 'TL', '630': 'PR', '634': 'QA',
  '642': 'RO', '643': 'RU', '646': 'RW', '682': 'SA', '686': 'SN',
  '694': 'SL', '699': 'IN', '700': 'SG', '702': 'SK', '703': 'SK',
  '704': 'VN', '706': 'SO', '710': 'ZA', '716': 'ZW', '724': 'ES',
  '728': 'SS', '729': 'SD', '732': 'EH', '736': 'SD', '740': 'SR',
  '748': 'SZ', '752': 'SE', '756': 'CH', '760': 'SY', '762': 'TJ',
  '764': 'TH', '768': 'TG', '780': 'TT', '784': 'AE', '788': 'TN',
  '792': 'TR', '795': 'TM', '800': 'UG', '804': 'UA', '807': 'MK',
  '818': 'EG', '826': 'GB', '834': 'TZ', '840': 'US', '854': 'BF',
  '858': 'UY', '860': 'UZ', '862': 'VE', '887': 'YE', '894': 'ZM',
  '-99': 'XK', // Kosovo
  '031': 'AZ', '051': 'AM', '238': 'FK', '499': 'ME', '688': 'RS',
  '705': 'SI',
  '010': 'AQ', // Antarctica
  '044': 'BS', // Bahamas
  '090': 'SB', // Solomon Islands
  '162': 'CX', // Christmas Island
  '166': 'CC', // Cocos Islands
  '174': 'KM', // Comoros
  '184': 'CK', // Cook Islands
  '258': 'PF', // French Polynesia
  '296': 'KI', // Kiribati
  '304': 'GL', // Greenland
  '312': 'GP', // Guadeloupe
  '316': 'GU', // Guam
  '474': 'MQ', // Martinique
  '492': 'MC', // Monaco
  '520': 'NR', // Nauru
  '570': 'NU', // Niue
  '574': 'NF', // Norfolk Island
  '580': 'MP', // Northern Mariana Islands
  '583': 'FM', // Micronesia
  '584': 'MH', // Marshall Islands
  '585': 'PW', // Palau
  '612': 'PN', // Pitcairn
  '638': 'RE', // Réunion
  '652': 'BL', // Saint Barthélemy
  '654': 'SH', // Saint Helena
  '659': 'KN', // Saint Kitts and Nevis
  '660': 'AI', // Anguilla
  '662': 'LC', // Saint Lucia
  '666': 'PM', // Saint Pierre and Miquelon
  '670': 'VC', // Saint Vincent and the Grenadines
  '674': 'SM', // San Marino
  '678': 'ST', // São Tomé and Príncipe
  '690': 'SC', // Seychelles
  '744': 'SJ', // Svalbard and Jan Mayen
  '772': 'TK', // Tokelau
  '776': 'TO', // Tonga
  '796': 'TC', // Turks and Caicos
  '798': 'TV', // Tuvalu
  '850': 'VI', // US Virgin Islands
  '876': 'WF', // Wallis and Futuna
  '882': 'WS', // Samoa
};

// Equirectangular projection: lon/lat → SVG coordinates
// viewBox: 0 0 1000 500
function projectCoord([lon, lat]) {
  const x = ((lon + 180) / 360) * 1000;
  const y = ((90 - lat) / 180) * 500;
  return [Math.round(x * 10) / 10, Math.round(y * 10) / 10];
}

function ringToPath(ring) {
  return ring.map((coord, i) => {
    const [x, y] = projectCoord(coord);
    return `${i === 0 ? 'M' : 'L'}${x} ${y}`;
  }).join('') + 'Z';
}

function geometryToPath(geometry) {
  if (geometry.type === 'Polygon') {
    return geometry.coordinates.map(ringToPath).join('');
  } else if (geometry.type === 'MultiPolygon') {
    return geometry.coordinates.map(polygon =>
      polygon.map(ringToPath).join('')
    ).join('');
  }
  return '';
}

// Convert TopoJSON to GeoJSON features
const fc = feature(topoData, topoData.objects.countries);

const result = {};
for (const f of fc.features) {
  const numCode = String(f.id).padStart(3, '0');
  const alpha2 = numToAlpha2[numCode] || numToAlpha2[String(f.id)];
  if (!alpha2) {
    console.warn(`No alpha-2 mapping for numeric code: ${f.id} (${f.properties?.name || 'unknown'})`);
    continue;
  }
  // Skip Antarctica - too large and not useful
  if (alpha2 === 'AQ') continue;

  const pathData = geometryToPath(f.geometry);
  if (pathData) {
    // If we already have this code (e.g. duplicate mappings), concatenate paths
    result[alpha2] = result[alpha2] ? result[alpha2] + pathData : pathData;
  }
}

const outPath = join(__dirname, '../src/data/world-map-paths.json');
writeFileSync(outPath, JSON.stringify(result, null, 0));
console.log(`Generated ${Object.keys(result).length} country paths → ${outPath}`);
console.log(`File size: ${(readFileSync(outPath).length / 1024).toFixed(1)} KB`);
