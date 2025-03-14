document.addEventListener('DOMContentLoaded', function() {
    const searchForm = document.querySelector('.search-form');
    const searchInput = document.querySelector('.search-input');
    const searchButton = document.querySelector('.search-button');
    const searchIconButton = document.querySelector('.search-icon-button');
    let searchExpanded = false;

    function toggleSearch() {
        if (window.innerWidth <= 768) { // Check if we're in the small-screen state
            if (searchExpanded) {
                // Hide the input, show the icon
                searchInput.style.display = 'none';
                searchIconButton.style.display = 'inline-block';
                searchButton.style.display = 'none';
                searchExpanded = false;
            } else {
                // Show the input, hide the icon
                searchInput.style.display = 'inline-block';
                searchButton.style.display = 'inline-block';
                searchIconButton.style.display = 'none';
                searchInput.focus(); // Put the cursor in the input
                searchExpanded = true;
            }
        }
    }

     function handleResize() {
        if (window.innerWidth > 768 && searchExpanded) {
			searchInput.style.display = 'inline-block';
            searchButton.style.display = 'inline-block';
            searchIconButton.style.display = 'none';
        } else if(window.innerWidth <= 768 && searchExpanded){
			searchInput.style.display = 'inline-block';
            searchButton.style.display = 'none';
            searchIconButton.style.display = 'none';
		} else if (window.innerWidth <= 768 && !searchExpanded) {
            searchInput.style.display = 'none';
            searchButton.style.display = 'none';
			searchIconButton.style.display = 'inline-block';
		}
    }

    searchIconButton.addEventListener('click', function(event) {
        if (window.innerWidth <= 768) {
             event.preventDefault(); // Prevent form submission on icon click
             toggleSearch();
        } //else form will submit normally
    });

    window.addEventListener('resize', handleResize);
});
