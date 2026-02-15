# ACC Final Response Attachment Downloader

Bulk downloads "Final Response" attachments from Autodesk Construction Cloud (ACC) **Submittal item detail** PDF exports.

---

## Overview

This utility parses Autodesk Construction Cloud submittal exports and automatically retrieves files listed under the **Final Response → Attachments** section.

The script:

• Reads ACC "Submittal item detail" PDFs  
• Detects Final Response attachment blocks  
• Extracts Autodesk attachment URLs embedded in the PDF  
• Matches URLs to attachment names  
• Downloads attachments  
• Normalises filenames for readability  
• Prevents duplicate filename collisions  

---

## Required Input File

Only the following ACC export is supported:

**Export Type:** Submittal item detail  
**Format:** PDF  

Other PDFs (drawings, specs, reports, etc.) will be rejected.

---

## How to Generate the Correct PDF in ACC

Access ACC  
→ Go to **Submittals**  
→ Select submittals  
→ Click **Export**  
→ Choose **Submittal item detail**  

---

## Features

• Validates correct ACC export type  
• Works with a single PDF or a folder of PDFs  
• Downloads all Final Response attachments  
• Human-readable filename cleanup  
• Six-digit spec code formatting (XX XX XX)  
• Duplicate-safe naming (_1, _2, etc.)  
• Handles missing Content-Disposition headers  

---

## Author
This tool was designed and developed by Jai Banala.
All credit for authorship and implementation belongs to the creator.