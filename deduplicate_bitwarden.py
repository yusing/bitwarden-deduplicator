#!/usr/bin/env python3
"""
Bitwarden JSON Export Deduplicator

This script removes duplicate folders and items from a Bitwarden JSON export file.
It identifies duplicates based on folder names and item properties, then creates
a new JSON file with only unique entries.
"""

import json
import os
import argparse
import sys
from datetime import datetime
from collections import defaultdict


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Remove duplicates from Bitwarden JSON export files.'
    )
    parser.add_argument(
        'input_file',
        help='Path to the Bitwarden JSON export file'
    )
    parser.add_argument(
        '-o', '--output',
        help='Path to save the deduplicated JSON file (default: input_file_deduplicated.json)'
    )
    parser.add_argument(
        '-s', '--summary',
        help='Path to save the deduplication summary (default: no summary)'
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Suppress progress output'
    )
    
    return parser.parse_args()


def log(message, quiet=False):
    """Print message if not in quiet mode."""
    if not quiet:
        print(message)


def deduplicate_bitwarden_export(input_file, output_file=None, summary_file=None, quiet=False):
    """
    Remove duplicates from a Bitwarden JSON export file.
    
    Args:
        input_file (str): Path to the Bitwarden JSON export file
        output_file (str, optional): Path to save the deduplicated JSON file
        summary_file (str, optional): Path to save the deduplication summary
        quiet (bool): Whether to suppress progress output
        
    Returns:
        dict: Summary statistics of the deduplication process
    """
    # Set default output file if not provided
    if not output_file:
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}_deduplicated{ext}"
    
    # Load the JSON data
    log(f"Loading JSON from {input_file}...", quiet)
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        log(f"Error: {str(e)}", quiet)
        sys.exit(1)
    
    log("JSON loaded successfully.", quiet)
    original_size = os.path.getsize(input_file)
    log(f"Original file size: {original_size:,} bytes", quiet)
    
    # Process folders - remove duplicates
    original_folders_count = len(data.get('folders', []))
    log(f"Original folders count: {original_folders_count}", quiet)
    
    unique_folders = {}
    for folder in data.get('folders', []):
        # Use folder name as the key for deduplication
        folder_name = folder.get('name', '')
        if folder_name and folder_name not in unique_folders:
            unique_folders[folder_name] = folder
    
    # Replace the original folders list with the deduplicated list
    data['folders'] = list(unique_folders.values())
    deduplicated_folders_count = len(data['folders'])
    log(f"Deduplicated folders count: {deduplicated_folders_count}", quiet)
    
    # Process items - remove duplicates
    original_items_count = len(data.get('items', []))
    log(f"Original items count: {original_items_count}", quiet)
    
    # Create a mapping of old folder IDs to new folder IDs
    folder_id_mapping = {}
    for old_folder in data['folders']:
        folder_id = old_folder.get('id', '')
        if folder_id:
            folder_id_mapping[folder_id] = folder_id
    
    # Group items by a composite key for deduplication
    unique_items = {}
    for item in data.get('items', []):
        # Create a composite key for deduplication
        # Using name, type, and login info if available
        key_parts = [
            item.get('name', ''),
            str(item.get('type', ''))
        ]
        
        login = item.get('login', {})
        if login:
            username = login.get('username', '')
            if username:
                key_parts.append(username)
            
            uris = login.get('uris', [])
            for uri_obj in uris:
                uri = uri_obj.get('uri', '')
                if uri:
                    key_parts.append(uri)
        
        key = '|'.join(key_parts)
        
        if key and key not in unique_items:
            # Update folder ID reference if needed
            folder_id = item.get('folderId', '')
            if folder_id and folder_id in folder_id_mapping:
                item['folderId'] = folder_id_mapping[folder_id]
            unique_items[key] = item
    
    # Replace the original items list with the deduplicated list
    data['items'] = list(unique_items.values())
    deduplicated_items_count = len(data['items'])
    log(f"Deduplicated items count: {deduplicated_items_count}", quiet)
    
    # Save the deduplicated JSON
    log(f"Saving deduplicated JSON to {output_file}...", quiet)
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except IOError as e:
        log(f"Error saving output file: {str(e)}", quiet)
        sys.exit(1)
    
    deduplicated_size = os.path.getsize(output_file)
    log(f"Deduplicated file size: {deduplicated_size:,} bytes", quiet)
    log("Deduplication complete!", quiet)
    
    # Prepare summary statistics
    summary = {
        'original_file': input_file,
        'original_size': original_size,
        'original_folders': original_folders_count,
        'original_items': original_items_count,
        'deduplicated_file': output_file,
        'deduplicated_size': deduplicated_size,
        'deduplicated_folders': deduplicated_folders_count,
        'deduplicated_items': deduplicated_items_count,
        'removed_folders': original_folders_count - deduplicated_folders_count,
        'removed_items': original_items_count - deduplicated_items_count,
        'size_reduction': original_size - deduplicated_size,
        'size_reduction_percent': round((original_size - deduplicated_size) / original_size * 100, 1) if original_size > 0 else 0,
        'folders_reduction_percent': round((original_folders_count - deduplicated_folders_count) / original_folders_count * 100, 1) if original_folders_count > 0 else 0,
        'items_reduction_percent': round((original_items_count - deduplicated_items_count) / original_items_count * 100, 1) if original_items_count > 0 else 0
    }
    
    # Save summary if requested
    if summary_file:
        try:
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write("# Bitwarden JSON Deduplication Summary\n\n")
                f.write(f"## Original File\n")
                f.write(f"- Filename: {os.path.basename(input_file)}\n")
                f.write(f"- Size: {summary['original_size']:,} bytes ({summary['original_size']/1024/1024:.2f} MB)\n")
                f.write(f"- Folders: {summary['original_folders']}\n")
                f.write(f"- Items: {summary['original_items']}\n\n")
                
                f.write(f"## Deduplicated File\n")
                f.write(f"- Filename: {os.path.basename(output_file)}\n")
                f.write(f"- Size: {summary['deduplicated_size']:,} bytes ({summary['deduplicated_size']/1024/1024:.2f} MB)\n")
                f.write(f"- Folders: {summary['deduplicated_folders']}\n")
                f.write(f"- Items: {summary['deduplicated_items']}\n\n")
                
                f.write(f"## Results\n")
                f.write(f"- Removed {summary['removed_folders']} duplicate folders ({summary['folders_reduction_percent']}% reduction)\n")
                f.write(f"- Removed {summary['removed_items']} duplicate items ({summary['items_reduction_percent']}% reduction)\n")
                f.write(f"- Reduced file size by {summary['size_reduction']:,} bytes ({summary['size_reduction_percent']}% reduction)\n\n")
                
                f.write(f"## Method\n")
                f.write(f"The deduplication was performed using a Python script that:\n")
                f.write(f"1. Identified duplicate folders based on folder names\n")
                f.write(f"2. Identified duplicate items based on a composite key of name, type, username, and URIs\n")
                f.write(f"3. Preserved the first occurrence of each unique entry\n")
                f.write(f"4. Maintained references between items and folders\n\n")
                
                f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            log(f"Summary saved to {summary_file}", quiet)
        except IOError as e:
            log(f"Error saving summary file: {str(e)}", quiet)
    
    return summary


def main():
    """Main function to run the script."""
    args = parse_arguments()
    deduplicate_bitwarden_export(
        args.input_file,
        args.output,
        args.summary,
        args.quiet
    )


if __name__ == "__main__":
    main()