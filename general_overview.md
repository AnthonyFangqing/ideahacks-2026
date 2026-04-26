# Idea for UCLA Ideahacks 2026

A digital ebook bookshelf/library “kiosk”  
Store ebooks  
load ereader  
display favorite shelf  
share books

Basically, a furniture item that displays and allows users to interact with eBooks fluidly like modern books, combining the old art form of long-form reading with modern technologies like eBooks

the Kiosk will store eBooks, allow users to easily transfer books to and from and ereaders and the kiosk (“bookshelf”)  
Allow family members to interact with the kiosk and share books more easily

# Design Constraints/Dimensions

all devices can be hardwired to each other.  
all devices will be powered

# Devices

## eReader

can dock into the kiosk to transfer files to and from the kiosk, and link up to the kiosk in any way possible

## Raspberry Pi

Powers the kiosk → the Pi is the main computer that handles all computing needs for the kiosk  
Can use usb, ethernet, hardware, etc.

Runs the application’s server

## Android Tablet

The kiosk screen (this probably should be a touchscreen monitor instead but we don’t have that so oh well)

We need:

- a display  
- a touchscreen  
- we might use the speakers  
- haptics would be fun

Displays the frontend client for the application

# Architecture

Raspberry Pi runs the application server that manages the entire state of the application. The Pi connects and manages all hardware, the eReader connects to the Pi

The tablet runs a web interface that talks to the server to have the frontend for the user to use.  
(Either a website or a dedicated app — something that talks to the server)

## Tech

Typescript  
Pnpm  
Vite  
Biome  
Vitest, husky, (this is a hackathon, we just need it to work)

## Server
Flask server using as much of calibre as possible

## Frontend V1 – website

Vite, React 

# Functionality

- move books from Raspberry Pi drive to the ereader storage. drag and drop preferred  
- go into “display mode”, where it displays the covers of books favorited by the user (will need to grab this image from the epub)  
- a connection to Project Gutenberg or some other way to search for and find ebooks would be nice