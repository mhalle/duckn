# Rendition Extension for duckn

**Extension name:** `rendition`
**Version:** 0.1
**Status:** Draft — superseded as an in-file design (see below)

> **Superseded as an in-file design; retained as source material.** This document was written under a design in which meaning and presentation were peer in-file extensions and `seg` was reduced to value-to-entity bindings. That design was not adopted. The in-file format is defined by `segmentation-ext-spec.md` (identity, roles, `label_values`, and one recommended `color` per segment) and `presentation-extension.md` (recommended grayscale windows and inversion). **Nothing in this document describes fields that a duckn file carries, and no reader or writer should implement it as an extension.** It is kept because the external-document layer sketched in §7.2 of the segmentation draft — groups and hierarchy, claims, inexact correspondences, stylesheets — will be specified from this material. Where this document and §7 of that draft disagree, that draft wins. In particular: `seg` 0.8 does **not** reference entities by id; colors are **not** moved into a rendition; there is no in-file `semantic` or `rendition` extension and no in-file default rendition; format mappings for segment colors and display windows are those of the two documents named above, not the ones here; and the coded-entry registry is `seg.terminologies`.

---

## 1. Purpose

This document defines the `rendition` extension for the duckn convention. It carries **choices about presentation** — how the data an array describes should be shown — separately from what that data *is* and from which data is which.

Color, opacity, visibility, stacking order, and a grayscale image's window are choices. The same liver is shown one way in a clinical view, another in a publication, and a third in a colorblind-safe palette, and none of those changes what the liver is. So appearance does not belong with the definitions in the `semantic` extension, which say what entities are, nor with the bindings in extensions such as `seg`, which say which values belong to which entity. It changes for its own reason — per viewer, per user, per publication — and is owned by a different authority, so it gets its own extension.

One rule shapes the whole design: **an in-file rendition is an external stylesheet that happens to be embedded.** There is one schema. A rendition written into a file by the author who made the data is the author's *recommended* rendition, in the sense DICOM gives that word, and a document applied from outside — a site's house style, a colorblind-safe palette, a publication style — has the same shape and merges with it by fixed rules (§7). Nothing in this extension works only from inside a file.

The duckn convention itself carries no display hints (§8 of the convention). This extension is where those hints go: a reader that does not recognize it ignores it, and loses nothing about what the data means.

---

## 2. Model

### Renditions

A file may carry several **renditions** — "clinical," "organs as one unit," "bone window" — each a complete, named way of showing the array. Rendition ids are their own namespace, and exactly one rendition is the **default**: the one a viewer shows when the user has not chosen, and the one an exporter reads when writing a format that carries a single color per segment.

A rendition does not need to restate everything. Where the selected rendition says nothing about a property, the default rendition's value applies, and where neither says anything, the viewer chooses (§5.5). That single step of fallback makes every non-default rendition an overlay on the default: "the same, but hide the artifact" is a one-rule rendition.

### Vocabularies

What a rendition can say depends on what kind of data it styles. A labelmap has segments to color and stack; a CT has samples to window. Each kind is a **vocabulary**, with a block of its own inside every rendition and a version line of its own:

| Vocabulary | Styles | Defined in |
|------------|--------|------------|
| `segments` | data bound to entities — a labelmap's segments, a probability array's classes | §5 |
| `image` | scalar samples shown as intensities | §6 |

Vocabularies change at very different rates. A window has been a center and a width since DICOM's first edition; segment styling will grow for years. Versioning each on its own line (§3.1) lets a reader apply the parts of a rendition it understands and skip the rest, so an image viewer keeps working through every revision of segment styling.

### Rules and declarations

Styling follows the model of CSS, which anyone who has written a stylesheet already knows. A **rule** pairs a **selector**, which says what the rule applies to, with a **declaration block**, which says what to set:

```json
{ "select": { "entity": "tumor" }, "style": { "color": "#cc3333", "opacity": 0.7 } }
```

A declaration block is a set of independent **properties**. `color` is one property among several — `opacity` and `display` are two others in this version — and each property is resolved on its own: one rule may set a structure's color while another sets its opacity, and neither disturbs the other (§5.5). A later version adds properties to a vocabulary without changing the shape of a rule, and a reader that does not know a property ignores that declaration and keeps the rest (§4.3).

Every property has an **initial value**, and a small set of **keywords** — `initial` and `revert` in this version, with `inherit` and `unset` reserved — may stand in place of any property's value (§4.2).

### What this extension does not do

It defines no entities, roles, or values: `semantic` and the binding extensions do, and a rendition only selects by them. It never changes data. It does not describe a scene — camera, slice position, zoom, which arrays are open together — which is viewer state. And it does not say how a renderer composites, shades, or interpolates; it says what the author chose, and a renderer realizes that choice with whatever method it uses.

---

## 3. Extension Fields

The `rendition` extension is declared under the `"duckn"` object's `"extensions"` key, as a peer of every other extension.

```json
{
  "duckn": {
    "version": "1.1",
    "extensions": {
      "rendition": {
        "version": "0.1",
        "vocabularies": { "segments": "0.1" },
        "default": "clinical",
        "renditions": {
          "clinical": {
            "segments": {
              "rules": [
                { "select": { "entity": "liver" }, "style": { "color": "#dd8265" } }
              ]
            }
          }
        }
      }
    }
  }
}
```

### 3.1 Top-Level Extension Fields

#### `version`

Required. The version of this extension specification, as a string: the version of the **container** — renditions, the default, fallback, keywords, and how stylesheets merge.

```json
"version": "0.1"
```

**Version semantics.** While the major version is `0`, the *minor* version may introduce breaking changes; this overrides the duckn convention's default rule that minor increments are additive. From 1.0 onward, major increments signal breaking changes and minor increments are additive. `version` must be a string: a JSON number `0.10` is the float 0.1, and a reader that parsed it that way would mistake a later file for an earlier one. The same rules apply to every vocabulary version.

#### `vocabularies`

Required. An object declaring every vocabulary used by any rendition in the extension, keyed by vocabulary name, with the version of that vocabulary as a string.

```json
"vocabularies": { "segments": "0.1", "image": "0.1" }
```

This follows the convention's own pattern for extensions: the declaration is made once, at the top, and the data appears where it is used. A reader that does not know a declared vocabulary, or does not support its version, skips that vocabulary's block in every rendition and applies the rest.

#### `default`

The id of the default rendition. Required in an extension on an array, where it must name a key of `renditions`. Optional in a standalone document (§7.1).

```json
"default": "clinical"
```

#### `renditions`

Required. An object whose keys are rendition ids and whose values are rendition objects (§3.2). Keys are unordered: a rendition's position means nothing, and `default` says which one comes first.

A rendition id is a token: it must be non-empty and must not contain `.`, `/`, or `#`, the same rule the `semantic` extension applies to entity ids and for the same reason — its qualified name is `rendition.renditions.<id>`.

#### `terminologies`

Present only in a standalone document (§7.1), where it registers the coding systems its selectors name, with the same fields as the `semantic` extension's registry. In an extension on an array it is absent: scheme keys in selectors resolve against the array's `semantic.terminologies`.

### 3.2 Rendition Object Fields

#### `name`

The human-readable name of the rendition, as a viewer would list it.

```json
"name": "Organs as one unit"
```

#### Vocabulary blocks

Every other key of a rendition object is the name of a vocabulary declared in `vocabularies`, and its value is that vocabulary's block: a `segments` block (§5) or an `image` block (§6). A rendition carries a block only for the vocabularies it has something to say about.

```json
"clinical": {
  "name": "Clinical",
  "image": { "window": { "center": 40, "width": 400 } },
  "segments": {
    "paint": ["liver", "tumor"],
    "rules": [
      { "select": { "entity": "tumor" }, "style": { "color": "#cc3333" } }
    ]
  }
}
```

---

## 4. Declarations

### 4.1 Declaration Blocks

A declaration block is a JSON object. Each key is a property defined by the vocabulary the block belongs to, and each value is either a value valid for that property or a keyword (§4.2). A `style` in a `segments` rule is a declaration block; an `image` block is one directly.

```json
"style": { "color": "#cc3333", "opacity": 0.7, "display": "auto" }
```

Properties are independent. Each is resolved separately (§5.5), so a block that sets `opacity` and not `color` leaves color to whatever else applies. Omitting a property is not the same as setting it: an omitted property takes part in no resolution, while a property set to `initial` wins and yields the initial value.

### 4.2 Keywords

The CSS-wide keywords `initial`, `inherit`, `unset`, and `revert` are reserved as values of every property in every vocabulary. No property may define one of them as an ordinary value. Reserving them in all properties now is what lets a later version give them meaning without changing the meaning of any file that is valid today. Keywords are lowercase strings and are compared exactly.

This version defines two:

| Keyword | Meaning |
|---------|---------|
| `initial` | The property's initial value, as stated with each property. A declaration of `initial` takes part in resolution like any other value, so in the selected rendition it wins over the default rendition. |
| `revert` | Withdraws the rendition's declaration: the property resolves as if this rendition declared nothing for it. In a non-default rendition, the default rendition's value applies; in the default rendition, the initial value does. |

`inherit` and `unset` are reserved and undefined. Writers must not write them, and readers treat them as invalid declarations (§4.3). `inherit` waits on a question this version does not answer: what a unit's parent is, when the membership graph lets an entity belong to several groups (§11).

The keywords are CSS's own, with the meanings CSS gives them mapped onto this model. CSS's `revert` rolls a property back to the previous *origin* of style, and here the origins are the viewer, the default rendition, and the selected rendition, in that order.

### 4.3 Invalid and Unknown Declarations

A rendition is read the way a browser reads a stylesheet: what cannot be understood is dropped at the smallest scope that keeps the rest correct.

- A declaration of a property the reader does not know is ignored.
- A declaration whose value is not valid for its property, or is a keyword this version does not define, is ignored. The rest of its block still applies.
- A rule whose selector the reader cannot evaluate — an unknown selector form — is ignored whole, because applying its declarations to a guess at what it selects could style the wrong thing.
- A vocabulary block the reader does not support is skipped (§3.1).

Writers must not rely on any of this: a writer writes only properties, values, and selectors that its declared versions define. The rules exist so that a file written against a later version degrades to the parts an earlier reader understands, rather than failing.

### 4.4 Colors

A color is a **CSS color string**. The syntax carries the color space: `#dd8265` is sRGB by definition, `color(display-p3 0.9 0.2 0.2)` is Display P3, and `oklch(0.7 0.12 40)` is Oklab's cylindrical form. That is what makes a color's meaning unambiguous, which a bare triple of numbers never is — `[0.87, 0.51, 0.40]` could be sRGB, linear light, or anything else, and nothing says which.

An unrestricted "CSS color" would require a full CSS parser of every reader, so the grammar is profiled:

| | Writers | Readers |
|---|---|---|
| **must** | write an sRGB color that fits 8 bits per channel as hex: `#rrggbb`, or `#rrggbbaa` with alpha; write a color whose source is CIELab as `lab()` (§9.2) | accept hex (`#rrggbb`, `#rrggbbaa`), `rgb()`, and `lab()` |
| **may** | write any other *absolute* color — `rgb()`, `hsl()`, `hwb()`, `lab()`, `lch()`, `oklab()`, `oklch()`, `color()` in a predefined space, or a named color — for a wider gamut or more precision | accept the full absolute color grammar of CSS Color Level 4 |
| **never** | context-dependent forms: `currentColor`, system colors, `color-mix()`, relative color syntax | — |

A value that does not parse is an invalid declaration (§4.3): the color is absent, and the next applicable declaration or the viewer supplies one.

Alpha arrives with the syntax, in `#rrggbbaa` or any functional form's `/ alpha`. It defaults to opaque, and the `opacity` property multiplies it (§5.4).

A color states what the color *is*; converting it to what a consumer needs is the consumer's business. A display pipeline wants the display's space; compositing and shading are correct only on linear-light values, so a renderer that does either converts to linear light first. None of those conversions is stored.

---

## 5. The `segments` Vocabulary

**Vocabulary name:** `segments` — **Version:** 0.1

This vocabulary styles data bound to entities: the label values of a segmentation, and anything else a binding extension attaches to the entities of the `semantic` extension (§6 of that extension). It applies to an array that carries `semantic` and at least one binding extension. It targets bindings that reference entities by id, which the segmentation extension does from its version 0.8; a 0.7 file carries a color on each segment instead, and its migration moves those colors into a default rendition.

A `segments` block has two fields, both optional:

| Field | Description |
|-------|-------------|
| `paint` | The units to draw, in stacking order (§5.1) |
| `rules` | An array of rules (§5.2) |

### 5.1 Paint Units

Rendition draws **units**. A unit is an entity, leaf or group, and its region is the entity's effective extent: a leaf's bound data, or for a group the union of its members' bound data. Styles attach to units, and every property of a unit is resolved once for the unit as a whole.

`paint` lists the units a rendition draws, bottom first: a unit drawn later is drawn over one drawn earlier, and where two regions overlap, the later one is on top.

```json
"paint": ["spleen", "liver", "tumor"]
```

Each entry is an entity id, or a selector object (§5.2) that stands for every entity it matches, in the order those entities appear in `semantic.entities`. An entity that appears in `paint` more than once is drawn at its last position. Painting a group draws its whole region as one unit — "color by level" in an atlas is a `paint` list of that level's groups — and painting a group and one of its members draws both, in the order listed.

Two further units are drawn first, beneath everything in `paint`, as the **floor**:

- bindings that carry a role and are bound to no entity — a background, or a region a binding extension marks unknown — in the binding extension's order;
- **undescribed** values: values present in the data that no binding describes. Nobody can list them, because nobody knows they are there until the data is read, so they are one unit, matched only by role and `undescribed` selectors.

Nothing in `paint` can be drawn beneath the floor, and nothing needs to be: a floor unit and an entity never share data.

When the selected rendition has no `paint`, the default rendition's `paint` is used. When neither has one, every entity bound by the array's binding extensions is drawn, in the order of its first binding. A binding with neither an entity nor a role has nothing a rendition can select, and a viewer draws it as it chooses.

### 5.2 Rules and Selectors

A rule is an object with two fields, both required:

| Field | Description |
|-------|-------------|
| `select` | A selector: an object with exactly one of the forms below |
| `style` | A non-empty declaration block (§4.1) |

```json
{ "select": { "designation": { "scheme": "SCT", "code": "10200004" } },
  "style": { "color": "#dd8265" } }
```

The selector forms:

| Form | Example | Matches |
|------|---------|---------|
| `entity` | `{ "entity": "liver" }` | the entity with that id, leaf or group |
| `designation` | `{ "designation": { "scheme": "SCT", "code": "10200004" } }` | every entity carrying a designation with that scheme and code. Without a `modifier` in the selector, a designation matches whatever its modifier; with one, the modifier must be equal |
| `mapping` | `{ "mapping": { "relation": "closeMatch", "scheme": "SCT", "code": "108369006" } }` | every entity whose `mappings` under that relation contain an entry with that scheme and code |
| `role` | `{ "role": "background" }` | every leaf unit with a binding carrying that role, and every floor unit of that role |
| `undescribed` | `{ "undescribed": true }` | the floor unit of undescribed values |

Selectors target entities by two routes and bindings by a third. `entity`, `designation`, and `mapping` go through the `semantic` level; `role` and `undescribed` read the binding level, where roles live. Which roles exist is each binding extension's business. A `role` selector matches the roles a binding declares and the roles a binding extension says a reader assigns — the segmentation extension's background at value 0 when no binding claims it is one.

`designation` and `mapping` are the portable forms: an entity id means something only in its own file, while "every liver designated SNOMED 10200004" means the same thing in every file. They are what make a stylesheet written with no file in hand possible (§7). A designation selector matches exact identifications only; a stylesheet that wishes to reach entities identified inexactly says so with `mapping`, naming the relation it accepts, as the `semantic` extension's guidance requires. Matching on `name` is deliberately absent: free text is what designations exist to replace.

A designation selector matches codes, not concepts. SNOMED's "Kidney" does not match an entity designated "Left kidney structure," because this extension does not reason over a terminology's hierarchy; a stylesheet that wants every kidney lists the codes it means.

### 5.3 Specificity and Order

When several rules declare the same property for the same unit, the most specific selector wins, and among equally specific selectors, the rule that comes later. From most to least specific:

1. `entity`
2. `designation` with a `modifier`
3. `designation`
4. `mapping`
5. `undescribed`
6. `role`

This is CSS's cascade: specificity first, then order. It is what makes a stylesheet overlay predictable. A rule for an entity id beats a house rule for its code; a house rule for left kidneys beats one for kidneys; and a site's stylesheet, whose rules come after the file's own (§7.2), wins wherever the two are equally specific.

### 5.4 Properties

#### `color`

The unit's color, as a CSS color string (§4.4).

```json
"color": "#dd8265"
```

Initial value: the viewer's choice.

#### `opacity`

A number in [0, 1] by which the alpha of the unit's color is multiplied. `0` draws the unit fully transparent; it is still drawn.

```json
"opacity": 0.7
```

Initial value: `1`.

#### `display`

Whether the unit is drawn. `"auto"` draws it; `"none"` removes it from the stack, as if it were absent from `paint`: it draws nothing, covers nothing, and is not a target for hit testing.

```json
"display": "none"
```

Initial value: `"auto"`.

`display` is not an opacity of zero, and the difference is visible. A unit with `opacity` 0 is still on the stack: it still owns the values it covers when the rendition is flattened to one color per value (§5.6), and a viewer toggling it back on restores the opacity it had. A unit with `display` `"none"` gives up its place entirely. Keeping the two apart is what lets a viewer's show-and-hide control leave every other property as it was.

### 5.5 Resolving a Rendition

To draw an array under a selected rendition *R*, with default rendition *D*:

1. **Units.** The floor units, then the entries of *R*'s `paint` — or *D*'s, or the implicit list — in order (§5.1).
2. **Declarations.** For each unit and each property, independently:
   - among *R*'s rules whose selector matches the unit and whose `style` validly declares the property, the most specific wins, and among equals the last (§5.3);
   - if none does, or the winner is `revert`, the same in *D*, when *D* is not *R*;
   - if none does there either, or that winner is `revert`, the property takes its initial value;
   - a winning `initial` yields the initial value.
3. **Drawing.** Every unit whose `display` is not `"none"` is drawn over the region it resolves to, in order, with its resolved `color` and `opacity`.

The result is fully determined by the file: which rendition, which units, in which order, with which values. Nothing is left to a tie-break a reader must invent.

### 5.6 Flattening to a Color Table

Many consumers can hold only one color per segment or per value: an exporter writing `.seg.nrrd` or DICOM SEG, or a renderer drawing a labelmap through a lookup table. Flattening a rendition gives them that:

- **Per value**, a value takes the resolved color of the last displayed unit whose region contains it. A value that no displayed unit covers has no color.
- **Per binding**, a binding takes the resolved color of the last displayed unit that contains it.

Flattening does not composite. A color table has one entry per value, so the unit drawn last over a value owns it, and a translucent unit does not blend with the one beneath. Where a target format carries alpha, a flattened color's alpha is its own alpha multiplied by `opacity`; where it carries none, alpha is dropped.

An exporter uses the default rendition unless it is told to use another.

---

## 6. The `image` Vocabulary

**Vocabulary name:** `image` — **Version:** 0.1

This vocabulary styles scalar samples shown as intensities: a CT, an MR, a probability map. It applies to an array with one scalar component per sample, and not to a labelmap (an array whose `intent` is `"label-map"`), whose values name segments rather than measure anything. An `image` block is a declaration block (§4.1) applied to the whole array.

### 6.1 Properties

#### `window`

A linear window, as an object with two numbers. `center` and `width` are in `sample_units`, applied to the values after `value_transforms` — the same values a reader computes as the array's quantity, in Hounsfield units for a calibrated CT. `width` must be greater than 0.

```json
"window": { "center": 40, "width": 400 }
```

The window maps `center - width/2` to the darkest displayed intensity and `center + width/2` to the brightest, linearly between and clamped outside. Up to the half-unit offsets DICOM specifies for integer data, this is DICOM's `LINEAR` VOI function.

Initial value: the viewer's choice.

A window is a keyword-capable value like any other: `"window": "initial"` restores the viewer's choice in a rendition that would otherwise take the default rendition's window.

---

## 7. Stylesheets

### 7.1 Standalone Documents

A **stylesheet** is a rendition written outside any file: a JSON document whose top-level object has the fields of the extension object (§3). It declares `version` and `vocabularies` like any rendition; it may omit `default`; and when any of its selectors names a scheme, it carries `terminologies` registering every scheme it names, because it has no file's registry to borrow.

A stylesheet may contain any vocabulary. A `segments` block applies to arrays that bind entities and an `image` block to arrays of scalar samples; a block that does not apply to a given array is not applied to it, and is not an error.

### 7.2 Applying a Document

Applying a stylesheet to an array means merging it into the array's rendition extension, rendition by rendition, keyed by id:

- **A rendition id the array already has** is overlaid. In each vocabulary block, the stylesheet's `rules` are appended after the array's own, so they win at equal specificity (§5.3); a `paint` in the stylesheet replaces the array's; an `image` block's declarations replace the array's property by property; a vocabulary block the array lacks is added.
- **A rendition id the array does not have** is added whole.
- **`default`**, when the stylesheet sets it, replaces the array's.

Several stylesheets apply in order, each to the result of the one before. Each document's blocks are read under the vocabulary versions that document declares.

Whether to apply a stylesheet at all is a viewer's or a user's choice; this extension fixes only what applying one *means*, so that two viewers applying the same stylesheet to the same file show the same thing. Applying never modifies the file. A writer that embeds the result writes a new rendition extension, which is thereafter that file's own choice.

### 7.3 Codes Across Files

Scheme keys are names local to a document: one file's `SCT` is another's `SNOMED`. In an extension on an array, selector schemes are keys of the array's `semantic.terminologies`; in a stylesheet, they are keys of its own `terminologies`, which say what each key names. A reader compares keys as strings. Where a stylesheet and a file use different keys for the same system — their registrations give the same `url` — a reader may treat the keys as equal, and should report that it did.

---

## 8. Consistency Rules

**Structure**

1. `version` and `vocabularies` are present. Every vocabulary version, like `version`, is a string.
2. Every key of `renditions` is non-empty and contains none of `.`, `/`, `#`.
3. In an extension on an array, `default` is present and names a key of `renditions`. In a stylesheet, `default` is optional, and names a key of `renditions` when present.
4. Every key of a rendition object other than `name` is a vocabulary declared in `vocabularies`.
5. `terminologies` is absent from an extension on an array. A stylesheet whose selectors name a scheme carries `terminologies`, and registers every scheme it names.

**Applicability**

6. A `segments` block in an extension on an array requires the array to carry `semantic` and a binding extension. An `image` block requires scalar samples, and does not appear on a labelmap.

**References**

7. In an extension on an array, every `entity` selector and every string in a `paint` list names an entity in the array's `semantic.entities`.

**Rules and declarations**

8. A rule has a `select` with exactly one selector form and a non-empty `style`.
9. Every declaration's value is valid for its property or is a keyword this version defines (§4.2). Writers do not write `inherit` or `unset`.
10. A color is written within the profile of §4.4.

Rules 1–10 are checkable from the rendition and the array's own metadata; none requires the data.

Writers should validate before serializing. Readers should not reject a file for what §4.3 tells them to ignore. A reader that finds `default` naming no rendition, or a `paint` entry naming no entity, should report the error and draw with viewer defaults, rather than choose a rendition or skip an entry on its own authority.

---

## 9. Mapping to Other Formats

### 9.1 `.seg.nrrd`

A `.seg.nrrd` file carries one color per segment and nothing else about appearance.

- **Import.** A converter writes one rendition — named after the source, for example `"slicer"` — declares it the default, and gives it one rule per segment, selecting the segment's entity by id, with `SegmentN_Color` converted to hex: each float channel `x` becomes `round(255 * x)`.
- **Export.** An exporter flattens the default rendition per binding (§5.6) and writes each channel as `n / 255` formatted with six significant digits, which is how 3D Slicer writes it, so a color that came from Slicer returns byte for byte. Any other color — a `lab()` transcribed from DICOM, a wide-gamut color, one finer than 8 bits — is converted to sRGB by CSS Color 4's conversion and clipped. `opacity` and `display` have no field in `.seg.nrrd` and are not exported.

`SegmentN_ColorAutoGenerated` describes the segment as Slicer holds it and stays with the segmentation extension's Slicer metadata.

### 9.2 DICOM

**Segment color.** A DICOM Segmentation's `RecommendedDisplayCIELabValue` (Type 3) maps to the `color` of the segment's entity in the default rendition. DICOM encodes CIELab as the ICC profile connection space, whose reference white is D50 — the same color space as CSS Color 4's `lab()`. The three 16-bit values are therefore transcribed, not converted:

- `L = v × 100 / 65535`
- `a = v × 255 / 65535 − 128`, and likewise `b`

and written as `lab(L a b)` with at least three decimal places, which is enough to recover the 16-bit integers exactly. `[39330, 30580, 41942]` becomes `"lab(60.014 -9.012 35.198)"`. The round trip to DICOM and back is lossless, and a color outside the sRGB gamut survives it. Writing hex instead would force a chromatic adaptation, a gamut clip, and 8-bit rounding, which is why §4.4 requires readers to accept `lab()`.

Export converts the flattened default rendition's color (§5.6) to `lab()` by the conversions CSS Color 4 defines, then scales to 16 bits; alpha, `opacity`, and `display` are not exported. A reader displaying a `lab()` color likewise uses CSS's conversion — Bradford adaptation from D50 to D65, then sRGB. This extension defines no color arithmetic of its own in either direction.

**Files in the wild.** dcmqi, and 3D Slicer through it, have long computed these values with a D65 reference white, so many existing DICOM Segmentations carry numbers that, read correctly, differ slightly from the color their author saw — a few 8-bit levels per channel for typical segment colors. A converter transcribes the numbers as written by default: the file says what it says, and correcting for a producer's presumed error is how a second error is introduced. A converter may offer compensation for files it can identify as produced that way, as an explicit option that is off by default.

**Window.** An image's `WindowCenter` and `WindowWidth` map to `image.window` in a rendition. The first pair becomes the default rendition; each further pair becomes a rendition of its own, named from `WindowCenterWidthExplanation` where present. DICOM applies the window to the output of the modality transformation, which is the quantity `value_transforms` computes, so the numbers carry over unchanged into `sample_units`. A non-linear VOI LUT has no counterpart in this version and stays among the preserved attributes described in `dicom-spec.md`.

### 9.3 Color Tables

Most color tables are keyed by value, and a value can be styled only through the entity it is bound to (§5.1). An importer therefore gives each row an entity and a binding, and puts its color in a rule selecting that entity.

| Source | Keyed by | Imports as |
|--------|----------|------------|
| FreeSurfer color table (`FreeSurferColorLUT.txt`, FastSurfer's `FastSurfer_ColorLUT.tsv`) | value; R, G, B as 0–255; a fourth column | `color` in hex. The fourth column is 0 on nearly every row of FreeSurfer's own table and is not imported as opacity |
| ITK-SNAP label description | value; R, G, B; alpha; visibility | `color`, `opacity`, and `display` `"none"` for a hidden label |
| BIDS `dseg.tsv` | value; `color` as hex | `color` directly |
| OME-Zarr `image-label` | `label-value`; `rgba` as four 0–255 integers | `color` as `#rrggbbaa` |
| 3D Slicer and dcmqi terminologies | a coded concept; `recommendedDisplayRGBValue` as 0–255 | a stylesheet of `designation` rules, one per concept, applying to any file whose entities carry those codes |

The last row is the reason portable selectors exist. A DICOM Segmentation with no recommended colors is colored by Slicer from its terminology; the same table, written once as a stylesheet, gives any duckn file the same colors through §7, with nothing special-cased.

---

## 10. Examples

### 10.1 A CT Abdomen Segmentation

The rendition for the entities of the first example in `semantic-ext-spec.md`: a liver, a lesion that overlaps it, a spleen, a motion-artifact region, and a group `organs` over the liver and spleen. The array is a labelmap, so it carries no `image` block; the window for the CT it segments is that array's own rendition (§10.4).

```json
{
  "version": "0.1",
  "vocabularies": { "segments": "0.1" },
  "default": "clinical",
  "renditions": {
    "clinical": {
      "name": "Clinical",
      "segments": {
        "paint": ["spleen", "liver", "tumor", "artifact"],
        "rules": [
          { "select": { "designation": { "scheme": "SCT", "code": "10200004" } },
            "style": { "color": "#dd8265" } },
          { "select": { "entity": "spleen" }, "style": { "color": "#9f7fb3" } },
          { "select": { "entity": "tumor" }, "style": { "color": "#cc3333", "opacity": 0.7 } },
          { "select": { "entity": "artifact" }, "style": { "color": "#808080", "opacity": 0.5 } }
        ]
      }
    },
    "lesion": {
      "name": "Lesion review",
      "segments": {
        "rules": [
          { "select": { "entity": "tumor" }, "style": { "opacity": 1 } },
          { "select": { "entity": "artifact" }, "style": { "display": "none" } }
        ]
      }
    },
    "organs": {
      "name": "Organs as one unit",
      "segments": {
        "paint": ["organs", "tumor"],
        "rules": [
          { "select": { "entity": "organs" }, "style": { "color": "#c8a2c8" } }
        ]
      }
    }
  }
}
```

**Clinical** shows all three selector routes a file uses most: the liver by its SNOMED code, which makes that rule portable; the spleen, tumor, and artifact by id. `paint` puts the tumor over the liver, so their overlap reads as lesion.

**Lesion review** has no `paint`, so it draws the clinical list. Its tumor rule sets only `opacity`; the tumor's color resolves from the default rendition, because properties resolve independently (§5.5). The liver and spleen are unmentioned and look exactly as they do in clinical. The artifact is removed from the stack.

**Organs as one unit** paints the group, so the liver and spleen are drawn as one lilac region, then the tumor over it, colored and made translucent by the default rendition's rule. The artifact is not in this `paint` list and is not drawn.

### 10.2 Hiding Versus Transparency

A layer in which value 1 is liver only, 2 is tumor only, and 3 is where they overlap: the liver is bound to {1, 3}, the tumor to {2, 3}, and `paint` is `["liver", "tumor"]`. Flattened to one color per value (§5.6), the two ways of making the tumor disappear give different tables:

| Tumor's style | Value 1 | Value 2 | Value 3 |
|---------------|---------|---------|---------|
| `{ "display": "none" }` | liver | no color | liver |
| `{ "opacity": 0 }` | liver | tumor, alpha 0 | tumor, alpha 0 |

With `display` `"none"`, the tumor gives up the overlap, and the liver is whole. With `opacity` 0, the tumor still owns the overlap and draws it invisibly, so the liver has a hole where the lesion is — which is what a reviewer asking "what is under the lesion?" does not want, and what one asking "where is the lesion?" might.

### 10.3 A House Stylesheet

A site's standing colors, written with no file in hand. Every selector is portable, so the stylesheet applies to any file whose entities carry these codes, and the last rule flags any value a writer forgot to describe.

```json
{
  "version": "0.1",
  "vocabularies": { "segments": "0.1" },
  "terminologies": {
    "SCT": { "name": "SNOMED Clinical Terms", "url": "http://snomed.info/sct" }
  },
  "renditions": {
    "clinical": {
      "segments": {
        "rules": [
          { "select": { "designation": { "scheme": "SCT", "code": "10200004" } },
            "style": { "color": "#dd8265" } },
          { "select": { "designation": { "scheme": "SCT", "code": "78961009" } },
            "style": { "color": "#9f7fb3" } },
          { "select": { "designation": { "scheme": "SCT", "code": "18639004" } },
            "style": { "color": "#b5651d" } },
          { "select": { "undescribed": true }, "style": { "color": "#ff00ff" } }
        ]
      }
    }
  }
}
```

Applied to the file of §10.1, it overlays the file's `clinical` rendition: its rules follow the file's, so its liver rule wins over the file's equally specific one, while the file's rules for the spleen, tumor, and artifact by id stay more specific than anything here. The liver color is the one 3D Slicer's general-anatomy terminology recommends for SNOMED 10200004; the others are the site's.

### 10.4 CT Windows

A CT array with three renditions and no segments. A viewer offers them by name; soft tissue is shown first.

```json
{
  "version": "0.1",
  "vocabularies": { "image": "0.1" },
  "default": "soft-tissue",
  "renditions": {
    "soft-tissue": { "name": "Soft tissue", "image": { "window": { "center": 40, "width": 400 } } },
    "lung": { "name": "Lung", "image": { "window": { "center": -600, "width": 1500 } } },
    "bone": { "name": "Bone", "image": { "window": { "center": 400, "width": 1800 } } }
  }
}
```

### 10.5 Minimal

One rendition, one color.

```json
{
  "version": "0.1",
  "vocabularies": { "segments": "0.1" },
  "default": "r1",
  "renditions": {
    "r1": {
      "segments": {
        "rules": [
          { "select": { "entity": "S1" }, "style": { "color": "#dd8265" } }
        ]
      }
    }
  }
}
```

---

## 11. Design Notes

**Why a separate extension.** Three things about a segment change for three different reasons, under three different authorities. Which values belong to it changes when the data is redrawn; what it is changes when a definition is revised; how it is shown changes per viewer, per user, and per publication, without the thing changing at all. `.seg.nrrd` packed all three into one record, and the segmentation extension inherited that through its version 0.7. Separating them gives each its own version line and its own external counterpart: the array for bindings, terminologies for meaning, color tables and stylesheets for appearance.

**Why the embedded rendition is a stylesheet.** A round trip through DICOM SEG or `.seg.nrrd` needs appearance inside the file, because both formats carry it there. A site's clinical conventions, a colorblind-safe palette, and a publication style need it outside any file. Two schemas would drift, and any feature available only in-file would be one no style author could use. So there is one schema, every reference in it has a portable form, and the in-file copy is simply the author's recommendation, merged with others by fixed rules.

**Why color is one property among several.** A rule is a selector and a declaration block, and `color` is one declaration in the block. Three consequences follow, and each would be lost if color were a field of the rule itself. Properties resolve independently, so one rule can make the tumor translucent while another colors it, and a viewer's hide control can remove a unit without disturbing its color or opacity. A vocabulary grows by adding a property, which changes no rule's shape and which an older reader ignores: a glass-shell rendering mode for translucent anatomy, per-language names for a viewer's labels, and a colormap for a scalar image are each one more property. And the selector and the declarations live in separate objects, so a new property can never collide with a selector's key, which a flat object mixing the two could not promise.

**Why CSS colors.** A triple of numbers is not a color until something says what space it is in, and the formats this convention exchanges with never say. The consequence is measured, not hypothetical: 8-bit sRGB values copied into glTF's linear `baseColorFactor` display 24 to 71 levels too light, and nothing in the source records which conversion was owed. A CSS color carries its space in its syntax, is what every web viewer already speaks, and offers perceptually uniform spaces for palettes that need them. The profile keeps readers small: hex is required of writers for the common case and is what every color-table format already stores, and everything context-dependent is excluded, because a file has no context to depend on.

**Why the keywords are reserved now.** A keyword is a string, and some future property's ordinary values will be strings. If `inherit` were not reserved across all properties from the start, a later version could not give it meaning without changing what some valid file says. `initial` and `revert` are defined because this model has exactly the structures they need — an initial value per property and an order of origins. `inherit` is not, because it needs a parent, and the membership graph is a directed acyclic graph in which an entity may belong to several groups. The segmentation extension's 0.7 rule — a leaf's color comes from the first containing group in array order — answered that with an order the `semantic` extension has since declared meaningless. Paint units cover the case that rule existed for: to color by level, paint the level's groups.

**Why styles attach to units.** A rendition draws regions, and a region is what an entity's bindings cover. Styling the unit rather than the values keeps every property of a painted group single-valued, and makes the overlap question — which color does a shared value get — a question of stacking order, answered by `paint`. Membership stays a set in the `semantic` extension, and the one thing its order could have meant, which member is drawn over which, lives here, where it is a choice.

**Why selectors reach two levels.** Stacking and portable styling are semantic: they need ids, groups, and codes. But the most common cross-file rule there is — show the unknown region in gray, flag anything undescribed — is about roles, which are facts about bindings. So selectors go through the semantic level by `entity`, `designation`, and `mapping`, and read the binding level by `role` and `undescribed`. Nothing here defines either level; the line between definition and choice holds because a rendition only selects.

**Why CSS's cascade.** Specificity first and order second is the model a style author brings, and it makes overlays compose predictably: an id beats a code, a lateralized code beats an unlateralized one, and a later document beats an earlier one where neither is more specific. A private precedence scheme would have to be learned and would be misremembered.

**Why vocabularies have their own versions.** The container, image styling, and segment styling will change at very different rates, and a reader should not lose the stable parts to churn in the unstable one. Declaring each vocabulary's version once, at the top, is the convention's own pattern for extensions, one level down, and it lets a reader know before applying a stylesheet whether it can. Separate extensions for image and segment rendition would have versioned independently too, but would have split a rendition: "clinical" would exist in two places, under one id, with nothing holding them together.

**Why one document for now.** Each vocabulary is versioned on the wire by `vocabularies`, not by which document describes it, so its text can move — the `image` vocabulary toward the convention, the `segments` vocabulary beside the segmentation extension — without changing any file. While all three are at 0.1 and change together, one document is easier to review.

**Why the window is in `sample_units`.** A window is chosen for a quantity: a soft-tissue window is centered at 40 HU and 400 HU wide, whatever the stored integers are. DICOM applies its window after the modality transformation for the same reason. And it is here, not in the convention, because the convention's `value_transforms` answers "what is this value" while a window answers "how should it look" — the no-display-hints rule, kept by putting display where it belongs.

**What is deliberately absent.** Scene state — camera, slice, zoom, which arrays are shown together — is viewer state, not a rendition of an array. Rule-based stacking ("abnormal structures over anatomy") is a rule system and waits for a consumer; explicit `paint` covers every case raised so far. Colormaps and opacity transfer functions for scalar images are future `image` properties. Renditions at the scope of a Zarr group, styling several arrays as one scene, wait on the convention defining groups. How to interpolate between colors, and in which space, belongs to the colormap properties when they come.
