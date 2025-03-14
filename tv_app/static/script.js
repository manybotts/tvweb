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

    let slideIndex = 0;

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

        for (i = 0; i < slides.length; i++) {
            slides[i].style.display = "none";
        }
        for (i = 0; i < dots.length; i++) {
            dots[i].className = dots[i].className.replace(" active", "");
        }

        if (slides.length > 0) {
            slides[slideIndex].style.display = "block";
            if (dots.length > 0) {
                dots[slideIndex].className += " active";
            }
        }
    }


    // Set initial background images for prev/next (assuming you have at least 3 slides)

    let slideInterval = setInterval(() => { plusSlides(1) }, 5000);
    showSlides(slideIndex);

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
