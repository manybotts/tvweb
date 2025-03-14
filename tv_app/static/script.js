document.addEventListener('DOMContentLoaded', function() {
const searchForm = document.querySelector('.search-form');
const searchInput = document.querySelector('.search-input');
const searchButton = document.querySelector('.search-button');
const searchText = document.querySelector('.search-text');
const searchIcon = document.querySelector('.search-icon');
let searchExpanded = false;

function toggleSearch() {
    if (window.innerWidth <= 768) { // Check if we're in the small-screen state
        if (searchExpanded) {
            // Hide the input, show the icon
            searchInput.style.display = 'none';
            searchText.style.display = 'none';
            searchIcon.style.display = 'inline-block';
            searchExpanded = false;
        } else {
            // Show the input, hide the icon
            searchInput.style.display = 'inline-block';
            searchText.style.display = 'inline-block';
            searchIcon.style.display = 'none';
            searchInput.focus(); // Put the cursor in the input
            searchExpanded = true;
        }
    }
}

 function handleResize() {
    if (window.innerWidth > 768 && searchExpanded) {
			searchInput.style.display = 'inline-block';
            searchText.style.display = 'inline-block';
            searchIcon.style.display = 'none';
        } else if(window.innerWidth <= 768 && searchExpanded){
			searchInput.style.display = 'inline-block';
            searchText.style.display = 'none';
            searchIcon.style.display = 'none';
		} else if (window.innerWidth <= 768 && !searchExpanded) {
            searchInput.style.display = 'none';
            searchText.style.display = 'none';
			searchIcon.style.display = 'inline-block';
		}
    }

    searchButton.addEventListener('click', function(event) {
        if (window.innerWidth <= 768) {
             event.preventDefault(); // Prevent form submission on icon click
             toggleSearch();
        } //else form will submit normally
    });

    window.addEventListener('resize', handleResize);
});
