
# PyFapello

PyFapello is a Python script for downloading galleries of images/videos from fapello.com.

# Usage

To use PyFapello, follow these steps:
1. Clone the repository:

```bash
git clone https://github.com/your-username/PyFapello.git
```
2. Navigate to the project directory:

```bash
cd PyFapello
```
3. Install the requirements
```py
pip install -r requirements.txt
```

4. Run the script with the following command:
```py

python main.py -l <fapello_link> -f <folder_name> -s
```
Replace <fapello_link> with the link to the gallery on fapello.com, and <folder_name> with the name of the folder where you want to save the downloaded files.

Optional argument -s or --save can be used to save the links to the database.


# Features

- Downloads images and videos from fapello.com galleries.
- Allows saving links to the database for backup and to download only new content.

# Requirements

- Python 3.x
requests library
tqdm library
argparse library
SQLite3 (for database functionality)

# How It Works

PyFapello utilizes the argparse library for command-line argument parsing. It accepts the fapello link (-l or --link), folder name (-f or --folder), and an optional flag -s or --save to save the links to the database.

The script downloads images and videos from the provided fapello link and saves them to the specified folder. It also provides a progress bar to track the download progress.

PyFapello uses a SQLite3 database to store the links. This serves two main purposes: backup of links and downloading only new content. By storing links in the database, the script can identify and download only the new content, avoiding duplicate downloads.
