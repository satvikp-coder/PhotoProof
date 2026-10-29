# PhotoProof — Image Forgery Detection and Localization

PhotoProof is an educational digital-image-forensics project exploring how classical image-processing techniques can surface regions that deserve closer inspection. The initial prototype provides **Error Level Analysis (ELA)** and **noise-residual visualization**. Later milestones will add copy-move matching, annotated-dataset evaluation, and learned localization.

> **Important:** PhotoProof is a research/learning tool, not a definitive authenticity detector. ELA and noise residuals can highlight normal compression, texture, resizing, or camera-processing artifacts. A highlighted region is not proof of forgery, and a clean result does not prove an image is authentic.

## Current features

- Upload a JPEG, PNG, WEBP, or BMP image in a local Streamlit interface.
- View the original image beside an ELA visualization.
- View a noise-residual visualization derived from a Gaussian-smoothed image.
- Inspect image dimensions and download-ready visual outputs from the interface.
- Reproducible, small unit tests for the image-processing functions.

## Planned roadmap

- [ ] Add copy-move detection with feature matching and geometric verification.
- [ ] Add a dataset loader for images and pixel-level manipulation masks.
- [ ] Evaluate localization with precision, recall, F1, and Intersection over Union (IoU).
- [ ] Add held-out test splits and false-positive testing on authentic images.
- [ ] Compare classical methods with a segmentation model as an optional extension.
- [ ] Add a structured, exportable analysis report and more comprehensive test coverage.

See [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for the project phases and evaluation plan.

## Quick start

### 1. Create and activate a virtual environment

**Windows PowerShell**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .
```

**macOS/Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .
```

Python 3.10 or newer is supported; Python 3.11 is a conservative choice for the initial setup.

### 2. Run the application

On Windows, you can double-click `run_photoproof.bat` after setup. Or run:

```bash
streamlit run app.py
```

Streamlit will print a local URL, usually `http://localhost:8501`. The initial version processes the uploaded image in the app process; do not upload sensitive images to a hosted deployment.

### 3. Run tests

```bash
pytest
```

## How the prototype works

### Error Level Analysis (ELA)

The input is converted to RGB, re-encoded as JPEG at a chosen quality, and compared pixel-by-pixel with that re-encoded copy. The difference is amplified for visualization. PNG and other non-JPEG inputs are also re-encoded to JPEG, so their ELA view is only a recompression experiment—not proof of editing.

### Noise-residual visualization

A Gaussian-smoothed image is subtracted from the input to expose high-frequency detail. The residual is amplified for display. Texture, sharpening, sensor noise, and resizing can all affect this map.

Neither method independently establishes that an image has been manipulated. Findings should be corroborated with additional signals and appropriate ground-truth evaluation.

## Repository structure

```text
PhotoProof/
├── app.py
├── src/photoproof/forensics.py
├── tests/test_forensics.py
├── docs/PROJECT_PLAN.md
├── data/README.md
├── requirements.txt
├── requirements-dev.txt
└── pyproject.toml
```

## Dataset and research notes

Start with a public image-forgery dataset that provides manipulation masks, such as [DEFACTO](https://defactodataset.github.io/), and review its terms before use. Keep original source images and their masks paired, prevent source-image leakage between training and test splits, and include authentic images to measure false positives. Do not commit large datasets or private photographs to Git; keep them outside the repository and document your local data paths.

## Responsible use

- Use only images you have permission to analyse.
- Do not present visual anomalies as conclusive evidence of fraud.
- Report the methods, thresholds, dataset split, and limitations alongside measurements.
- Do not use PhotoProof as the sole basis for legal, financial, or other high-stakes decisions.

## License

This project is released under the MIT License. See [`LICENSE`](LICENSE).
