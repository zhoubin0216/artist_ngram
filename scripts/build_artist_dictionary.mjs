import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const DATA_DIR = path.join(ROOT, "data", "wikidata");
const OUTPUT_DIR = path.join(ROOT, "outputs");
const PREVIEW_DIR = path.join(ROOT, ".tmp", "artist_dictionary_v1_previews");
const OUTPUT_PATH = path.join(OUTPUT_DIR, "艺术家字典_v1_首批.xlsx");
const FONT = "Arial";

const movementDefs = [
  { id: "M01", qid: "Q14378", slug: "neoclassicism", name: "Neoclassicism", start: 1760, end: 1850, ambiguity: "low" },
  { id: "M02", qid: "Q37068", slug: "romanticism", name: "Romanticism", start: 1780, end: 1850, ambiguity: "medium" },
  { id: "M03", qid: "Q10857409", slug: "realism", name: "Realism", start: 1840, end: 1880, ambiguity: "high" },
  { id: "M04", qid: "Q189458", slug: "academic_art", name: "Academic art", start: 1820, end: 1900, ambiguity: "medium" },
  { id: "M05", qid: "Q40415", slug: "impressionism", name: "Impressionism", start: 1860, end: 1886, ambiguity: "low" },
  { id: "M06", qid: "Q166713", slug: "post_impressionism", name: "Post-Impressionism", start: 1886, end: 1905, ambiguity: "low" },
  { id: "M07", qid: "Q164800", slug: "symbolism", name: "Symbolism", start: 1880, end: 1910, ambiguity: "high" },
  { id: "M08", qid: "Q34636", slug: "art_nouveau", name: "Art Nouveau", start: 1890, end: 1910, ambiguity: "low" },
  { id: "M09", qid: "Q166593", slug: "fauvism", name: "Fauvism", start: 1905, end: 1910, ambiguity: "low" },
  { id: "M10", qid: "Q80113", slug: "expressionism", name: "Expressionism", start: 1905, end: 1935, ambiguity: "medium" },
  { id: "M11", qid: "Q42934", slug: "cubism", name: "Cubism", start: 1907, end: 1925, ambiguity: "low" },
  { id: "M12", qid: "Q131221", slug: "futurism", name: "Futurism", start: 1909, end: 1944, ambiguity: "medium" },
  { id: "M13", qid: "Q6034", slug: "dada", name: "Dada", start: 1916, end: 1924, ambiguity: "medium" },
  { id: "M14", qid: "Q39427", slug: "surrealism", name: "Surrealism", start: 1924, end: 1950, ambiguity: "medium" },
  { id: "M15", qid: "Q128115", slug: "abstract_art", name: "Abstract art", start: 1910, end: 1950, ambiguity: "high" },
];

const movementByQid = new Map(movementDefs.map((m) => [m.qid, m]));

function value(binding, key) {
  return binding[key]?.value ?? null;
}

function qidFromUri(uri) {
  return uri?.split("/").pop() ?? "";
}

function yearFromWikidataTime(raw) {
  if (!raw) return null;
  const match = raw.match(/^([+-]?)(\d{4,})/);
  if (!match) return null;
  const year = Number(match[2]);
  return match[1] === "-" ? -year : year;
}

function modalYear(values) {
  const usable = values.filter((v) => Number.isFinite(v));
  if (!usable.length) return null;
  const counts = new Map();
  for (const v of usable) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0][0];
}

function claimValues(entity, property) {
  return (entity.claims?.[property] ?? [])
    .map((statement) => statement.mainsnak?.datavalue?.value)
    .filter((v) => v !== undefined && v !== null);
}

function claimYears(entity, property) {
  return claimValues(entity, property)
    .map((v) => yearFromWikidataTime(v.time))
    .filter((v) => Number.isFinite(v));
}

function addRowToArtist(artists, binding) {
  const artistId = qidFromUri(value(binding, "artist"));
  if (!artistId) return;
  if (!artists.has(artistId)) {
    artists.set(artistId, {
      artistId,
      canonicalName: value(binding, "artistLabel") ?? artistId,
      birthYears: [],
      deathYears: [],
      activeStarts: [],
      activeEnds: [],
      countries: new Set(),
      movementQids: new Set(),
      ulanIds: new Set(),
    });
  }
  const artist = artists.get(artistId);
  artist.birthYears.push(yearFromWikidataTime(value(binding, "birth")));
  artist.deathYears.push(yearFromWikidataTime(value(binding, "death")));
  artist.activeStarts.push(yearFromWikidataTime(value(binding, "activeStart")));
  artist.activeEnds.push(yearFromWikidataTime(value(binding, "activeEnd")));
  const country = value(binding, "countryLabel");
  const movementQid = qidFromUri(value(binding, "movement"));
  const ulan = value(binding, "ulan");
  if (country) artist.countries.add(country);
  if (movementQid) artist.movementQids.add(movementQid);
  if (ulan) artist.ulanIds.add(ulan);
}

function styleTitle(sheet, title, subtitle, width) {
  sheet.getRange(`A1:${width}1`).format.font = { name: FONT, size: 16, bold: true, color: "#111827" };
  sheet.getRange("A1").values = [[title]];
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${width}2`).format.font = { name: FONT, size: 10, italic: true, color: "#4B5563" };
  sheet.getRange(`A3:${width}3`).format.borders = { bottom: { style: "thin", color: "#9CA3AF" } };
  sheet.getRange("1:3").format.rowHeight = 22;
  sheet.getRange("1:1").format.rowHeight = 28;
}

function styleTable(sheet, headerRange, bodyRange) {
  const header = sheet.getRange(headerRange);
  header.format = {
    fill: "#1F4E78",
    font: { name: FONT, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  };
  header.format.rowHeight = 30;
  const body = sheet.getRange(bodyRange);
  body.format.font = { name: FONT, size: 10, color: "#111827" };
  body.format.verticalAlignment = "center";
}

const artists = new Map();
for (const movement of movementDefs) {
  const raw = JSON.parse(await fs.readFile(path.join(DATA_DIR, `${movement.slug}.json`), "utf8"));
  for (const binding of raw.results.bindings) addRowToArtist(artists, binding);
}

const sanityEntities = JSON.parse(await fs.readFile(path.join(DATA_DIR, "sanity_entities.json"), "utf8")).entities;
const manualAnchors = [
  { qid: "Q241732", movementQid: "Q10857409", countries: ["France"] },
  { qid: "Q104884", movementQid: "Q37068", countries: ["Kingdom of Prussia", "Sweden"] },
];
for (const anchor of manualAnchors) {
  if (artists.has(anchor.qid)) continue;
  const entity = sanityEntities[anchor.qid];
  artists.set(anchor.qid, {
    artistId: anchor.qid,
    canonicalName: entity.labels.en.value,
    birthYears: claimYears(entity, "P569"),
    deathYears: claimYears(entity, "P570"),
    activeStarts: claimYears(entity, "P2031"),
    activeEnds: claimYears(entity, "P2032"),
    countries: new Set(anchor.countries),
    movementQids: new Set([anchor.movementQid]),
    ulanIds: new Set(claimValues(entity, "P245")),
  });
}

const labelCounts = new Map();
for (const artist of artists.values()) {
  const key = artist.canonicalName.trim().toLocaleLowerCase("en");
  labelCounts.set(key, (labelCounts.get(key) ?? 0) + 1);
}

const finalArtists = [...artists.values()]
  .map((artist) => {
    const canonicalName = artist.canonicalName.trim().replace(/\s+/g, " ");
    const tokenCount = canonicalName.split(/\s+/).filter(Boolean).length;
    const movements = [...artist.movementQids]
      .map((qid) => movementByQid.get(qid))
      .filter(Boolean)
      .sort((a, b) => a.id.localeCompare(b.id));
    return {
      artistId: artist.artistId,
      canonicalName,
      tokenCount,
      birthYear: modalYear(artist.birthYears),
      deathYear: modalYear(artist.deathYears),
      activeStart: modalYear(artist.activeStarts),
      activeEnd: modalYear(artist.activeEnds),
      nationality: [...artist.countries].sort().join("; "),
      movementIds: movements.map((m) => m.id).join("; "),
      movementNames: movements.map((m) => m.name).join("; "),
      ulanId: [...artist.ulanIds].sort()[0] ?? "",
      duplicateLabel: (labelCounts.get(canonicalName.toLocaleLowerCase("en")) ?? 0) > 1,
      sourceUrl: `https://www.wikidata.org/wiki/${artist.artistId}`,
    };
  })
  .filter((artist) => artist.tokenCount >= 2 && !artist.duplicateLabel && artist.movementIds)
  .sort((a, b) => a.canonicalName.localeCompare(b.canonicalName, "en"));

if (finalArtists.length < 500 || finalArtists.length > 1500) {
  throw new Error(`Final artist count ${finalArtists.length} falls outside the requested 500–1500 range.`);
}

const incompleteArtists = finalArtists.filter((artist) =>
  !artist.artistId || !artist.canonicalName || !Number.isFinite(artist.birthYear) ||
  !Number.isFinite(artist.deathYear) || !artist.nationality || !artist.movementIds || !artist.ulanId
);
if (incompleteArtists.length) {
  throw new Error(`Found ${incompleteArtists.length} artists missing required baseline fields.`);
}

const finalArtistIds = new Set(finalArtists.map((a) => a.artistId));
const anchorChecks = [
  ["Vincent van Gogh", "Q5582"],
  ["William-Adolphe Bouguereau", "Q483992"],
  ["Rosa Bonheur", "Q241732"],
  ["Caspar David Friedrich", "Q104884"],
  ["Claude Monet", "Q296"],
].map(([name, qid]) => [name, qid, finalArtistIds.has(qid) ? "included" : "missing"]);

const movementEntities = JSON.parse(await fs.readFile(path.join(DATA_DIR, "movements_entities.json"), "utf8")).entities;
const movementRows = movementDefs.map((movement) => {
  const entity = movementEntities[movement.qid];
  const aliases = (entity.aliases?.en ?? [])
    .map((a) => a.value)
    .filter((a) => a.toLocaleLowerCase("en") !== movement.name.toLocaleLowerCase("en"));
  const uniqueAliases = [...new Set(aliases)].slice(0, 8).join("; ");
  const artistCount = finalArtists.filter((artist) => artist.movementIds.split("; ").includes(movement.id)).length;
  return [
    movement.id,
    movement.qid,
    movement.name,
    uniqueAliases,
    movement.start,
    movement.end,
    movement.ambiguity,
    artistCount,
    entity.descriptions?.en?.value ?? "",
    `https://www.wikidata.org/wiki/${movement.qid}`,
  ];
});

const artistRows = finalArtists.map((artist) => [
  artist.artistId,
  artist.canonicalName,
  artist.birthYear,
  artist.deathYear,
  artist.activeStart,
  artist.activeEnd,
  artist.nationality,
  artist.movementIds,
  artist.movementNames,
  artist.ulanId,
  artist.sourceUrl,
]);

const aliasRows = finalArtists.map((artist) => {
  const ambiguity = artist.tokenCount >= 3 ? "A" : "B";
  const reason = artist.tokenCount >= 3 ? "unique full name with 3+ tokens" : "unique two-token full name; sample check recommended";
  return [
    artist.artistId,
    artist.canonicalName,
    "full_name",
    artist.tokenCount,
    ambiguity,
    reason,
    "yes",
    artist.sourceUrl,
  ];
});

const workbook = Workbook.create();
const overview = workbook.worksheets.add("README");
const movementsSheet = workbook.worksheets.add("movements");
const artistsSheet = workbook.worksheets.add("artists");
const aliasesSheet = workbook.worksheets.add("artist_aliases");

overview.showGridLines = false;
overview.tabColor = "#1F4E78";
styleTitle(
  overview,
  "Artist dictionary baseline v1",
  "English-only Google Books N-gram candidate set based on the supplied construction plan",
  "H",
);
overview.getRange("A5:B9").values = [
  ["Metric", "Value"],
  ["Core movements", movementRows.length],
  ["Artists", finalArtists.length],
  ["Full-name queries", aliasRows.length],
  ["Data extracted", "2026-09-21"],
];
styleTable(overview, "A5:B5", "A6:B9");
overview.getRange("B6:B8").format.numberFormat = "#,##0";

overview.getRange("A11:B18").values = [
  ["Baseline rule", "Implementation"],
  ["Art-historical scope", "European painters associated with 15 selected movements from Neoclassicism through early abstract art."],
  ["Time overlap", "Known birth year ≤ 1925 and death year ≥ 1800, giving a practical overlap with the project’s c. 1800–1950 activity window."],
  ["Authority completeness", "Every row has a Wikidata QID, an English label, a European citizenship value, complete birth/death years, and a Getty ULAN ID."],
  ["Movement mapping", "Wikidata P135 is normalized to M01–M15. Caspar David Friedrich’s narrower German Romanticism classification is normalized to Romanticism."],
  ["Query policy", "v1 contains one canonical English full-name query per artist. One-token and duplicate full names are excluded."],
  ["Ambiguity heuristic", "A = unique full name with 3+ whitespace tokens. B = unique two-token full name. All rows remain candidates for later corpus-frequency checks."],
  ["Active years", "active_start and active_end are populated only when Wikidata P2031/P2032 are present; no dates are inferred."],
];
styleTable(overview, "A11:B11", "A12:B18");
overview.getRange("A20:C25").values = [
  ["Sanity-check artist", "Wikidata QID", "Baseline status"],
  ...anchorChecks,
];
styleTable(overview, "A20:C20", "A21:C25");
overview.getRange("A27:B31").values = [
  ["Source", "URL or note"],
  ["Artist and movement metadata", "https://www.wikidata.org/"],
  ["Query endpoint", "https://query.wikidata.org/"],
  ["Getty authority link", "Wikidata property P245 stores the Getty ULAN ID used as the authority completeness gate."],
  ["Next step", "Expand vetted aliases, run pre-birth frequency checks, and complete 20–30 Google Books N-gram sanity curves before promoting dictionary_v1."],
];
styleTable(overview, "A27:B27", "A28:B31");
overview.getRange("A5:C31").format.font = { name: FONT, size: 10, color: "#111827" };
overview.getRange("A5:C31").format.verticalAlignment = "center";
for (const headerRange of ["A5:B5", "A11:B11", "A20:C20", "A27:B27"]) {
  overview.getRange(headerRange).format.font = { name: FONT, size: 10, bold: true, color: "#FFFFFF" };
}
overview.getRange("B12:B18").format.wrapText = true;
overview.getRange("B28:B31").format.wrapText = true;
overview.getRange("A:A").format.columnWidth = 28;
overview.getRange("B:B").format.columnWidth = 92;
overview.getRange("C:C").format.columnWidth = 18;
overview.getRange("12:18").format.autofitRows();
overview.getRange("28:31").format.autofitRows();

movementsSheet.showGridLines = false;
movementsSheet.tabColor = "#4F81BD";
styleTitle(movementsSheet, "Movements", "15 normalized movement and style records with approximate historical ranges", "J");
const movementHeaders = [["movement_id", "wikidata_qid", "canonical_name", "aliases", "start_year", "end_year", "ambiguity", "artist_count", "description", "source_url"]];
movementsSheet.getRange("A4:J4").values = movementHeaders;
movementsSheet.getRange(`A5:J${4 + movementRows.length}`).values = movementRows;
styleTable(movementsSheet, "A4:J4", `A5:J${4 + movementRows.length}`);
movementsSheet.getRange(`D5:D${4 + movementRows.length}`).format.wrapText = true;
movementsSheet.getRange(`I5:I${4 + movementRows.length}`).format.wrapText = true;
movementsSheet.getRange(`E5:H${4 + movementRows.length}`).format.horizontalAlignment = "center";
movementsSheet.getRange(`E5:H${4 + movementRows.length}`).format.numberFormat = "0";
for (const [col, width] of Object.entries({ A: 13, B: 14, C: 22, D: 36, E: 11, F: 11, G: 12, H: 12, I: 48, J: 42 })) {
  movementsSheet.getRange(`${col}:${col}`).format.columnWidth = width;
}
movementsSheet.getRange(`A4:J${4 + movementRows.length}`).format.autofitRows();
const movementsTable = movementsSheet.tables.add(`A4:J${4 + movementRows.length}`, true, "MovementsTable");
movementsTable.style = "TableStyleMedium2";
movementsTable.showFilterButton = true;
movementsSheet.freezePanes.freezeRows(4);

artistsSheet.showGridLines = false;
artistsSheet.tabColor = "#70AD47";
styleTitle(artistsSheet, "Artists", `${finalArtists.length.toLocaleString("en-US")} authority-linked European painters for the baseline dictionary`, "K");
const artistHeaders = [["artist_id", "canonical_name", "birth_year", "death_year", "active_start", "active_end", "nationality", "movement_ids", "movement_names", "ulan_id", "source_url"]];
artistsSheet.getRange("A4:K4").values = artistHeaders;
artistsSheet.getRange(`A5:K${4 + artistRows.length}`).values = artistRows;
styleTable(artistsSheet, "A4:K4", `A5:K${4 + artistRows.length}`);
artistsSheet.getRange(`C5:F${4 + artistRows.length}`).format.numberFormat = "0";
artistsSheet.getRange(`C5:F${4 + artistRows.length}`).format.horizontalAlignment = "right";
artistsSheet.getRange(`G5:I${4 + artistRows.length}`).format.wrapText = true;
artistsSheet.getRange(`J5:J${4 + artistRows.length}`).format.horizontalAlignment = "center";
artistsSheet.getRange(`K5:K${4 + artistRows.length}`).format.font = { name: FONT, size: 9, color: "#1D4ED8" };
for (const [col, width] of Object.entries({ A: 13, B: 30, C: 11, D: 11, E: 11, F: 11, G: 26, H: 17, I: 34, J: 14, K: 43 })) {
  artistsSheet.getRange(`${col}:${col}`).format.columnWidth = width;
}
const artistsTable = artistsSheet.tables.add(`A4:K${4 + artistRows.length}`, true, "ArtistsTable");
artistsTable.style = "TableStyleMedium2";
artistsTable.showFilterButton = true;
artistsSheet.freezePanes.freezeRows(4);
artistsSheet.freezePanes.freezeColumns(2);

aliasesSheet.showGridLines = false;
aliasesSheet.tabColor = "#A5A5A5";
styleTitle(aliasesSheet, "Artist aliases", "Conservative v1 query dictionary using canonical full names only", "H");
const aliasHeaders = [["artist_id", "alias", "alias_type", "token_count", "ambiguity", "ambiguity_reason", "use_for_ngram", "source_url"]];
aliasesSheet.getRange("A4:H4").values = aliasHeaders;
aliasesSheet.getRange(`A5:H${4 + aliasRows.length}`).values = aliasRows;
styleTable(aliasesSheet, "A4:H4", `A5:H${4 + aliasRows.length}`);
aliasesSheet.getRange(`D5:E${4 + aliasRows.length}`).format.horizontalAlignment = "center";
aliasesSheet.getRange(`D5:D${4 + aliasRows.length}`).format.numberFormat = "0";
aliasesSheet.getRange(`F5:F${4 + aliasRows.length}`).format.wrapText = true;
aliasesSheet.getRange(`G5:G${4 + aliasRows.length}`).format.horizontalAlignment = "center";
aliasesSheet.getRange(`H5:H${4 + aliasRows.length}`).format.font = { name: FONT, size: 9, color: "#1D4ED8" };
for (const [col, width] of Object.entries({ A: 13, B: 30, C: 14, D: 12, E: 12, F: 45, G: 16, H: 43 })) {
  aliasesSheet.getRange(`${col}:${col}`).format.columnWidth = width;
}
const aliasesTable = aliasesSheet.tables.add(`A4:H${4 + aliasRows.length}`, true, "ArtistAliasesTable");
aliasesTable.style = "TableStyleMedium2";
aliasesTable.showFilterButton = true;
aliasesSheet.freezePanes.freezeRows(4);
aliasesSheet.freezePanes.freezeColumns(2);

await fs.mkdir(OUTPUT_DIR, { recursive: true });
await fs.mkdir(PREVIEW_DIR, { recursive: true });

const previews = [
  ["README", "A1:C31", "readme.png"],
  ["movements", "A1:J19", "movements.png"],
  ["artists", "A1:K24", "artists.png"],
  ["artist_aliases", "A1:H24", "artist_aliases.png"],
];
for (const [sheetName, range, filename] of previews) {
  const preview = await workbook.render({ sheetName, range, scale: 1.2, format: "png" });
  await fs.writeFile(path.join(PREVIEW_DIR, filename), new Uint8Array(await preview.arrayBuffer()));
}

const workbookCheck = await workbook.inspect({
  kind: "sheet,table",
  include: "id,name,range",
  maxChars: 5000,
  tableMaxRows: 3,
  tableMaxCols: 12,
});
const sampleCheck = await workbook.inspect({
  kind: "table",
  range: "artists!A4:K10",
  include: "values,formulas",
  tableMaxRows: 10,
  tableMaxCols: 12,
});
const errorCheck = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(OUTPUT_PATH);

console.log(JSON.stringify({
  outputPath: OUTPUT_PATH,
  artistCount: finalArtists.length,
  movementCount: movementRows.length,
  aliasCount: aliasRows.length,
  activeStartPopulated: finalArtists.filter((a) => a.activeStart !== null).length,
  activeEndPopulated: finalArtists.filter((a) => a.activeEnd !== null).length,
  anchorChecks,
  workbookCheck: workbookCheck.ndjson,
  sampleCheck: sampleCheck.ndjson,
  errorCheck: errorCheck.ndjson,
  previewDir: PREVIEW_DIR,
}, null, 2));
