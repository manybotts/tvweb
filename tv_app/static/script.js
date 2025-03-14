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
	// Initialize slideshow
    showSlides(slideIndex);

    let slideIndex = 0; // Moved outside DOMContentLoaded

    function plusSlides(n) {
        showSlides(slideIndex += n);
    }

    function currentSlide(n) {
        showSlides(slideIndex = n - 1);
    }

	function showSlides(n) {
		let i;
		let slides = document.getElementsByClassName("mySlides");
        let dots = document.getElementsByClassName("dot");

		if (n >= slides.length) { slideIndex = 0; }
		if (n < 0) { slideIndex = slides.length - 1; }

        // Remove 'active-slide' class from all slides
        for (let i = 0; i < slides.length; i++) {
            slides[i].classList.remove("active-slide");
        }

        //Add active class
         if (slides.length > 0) {
             slides[slideIndex].classList.add("active-slide");
          }
        // Ensure that dots exist before trying to access/modify them.
        for (i = 0; i < dots.length; i++) {
          dots[i].className = dots[i].className.replace(" active", "");
           }
         // Check if dots exist before trying to access them
        if (dots.length > 0) {
             dots[slideIndex].className += " active";
         }
	}

    // Automatic slideshow advance
    let slideInterval = setInterval(() => { plusSlides(1); }, 5000);
     // Pause slideshow on hover
    const slideshowContainer = document.querySelector(".slideshow-container");

    if(slideshowContainer){
        slideshowContainer.addEventListener('mouseover', () => {
            clearInterval(slideInterval);
        });

        slideshowContainer.addEventListener('mouseout', () => {
            slideInterval = setInterval(() => { plusSlides(1); }, 5000);
        });
    }
});
