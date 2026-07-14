# Data manifests

This directory stores reproducible source manifests—not downloaded images.

1. Download Fashionpedia images and annotations from its official project page.
2. Download the Open Images V7 label, class-description, and image-info CSV files.
3. Run `glance select-openimages` to make a candidate manifest; inspect its source URLs and licensing.
4. Download candidates, annotate locally, and keep the generated audit CSV outside Git.

The completed assignment corpus contains exactly 700 Fashionpedia records and 300 audited Open Images records (75 per required environment).

