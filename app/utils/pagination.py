"""
Utilities for API pagination.
"""
from typing import List, Dict, Any, TypeVar, Generic, Optional
from fastapi import Query, Depends
from pydantic import BaseModel


class PaginationParams:
    """
    Pagination parameters for API endpoints.
    
    This class is used as a FastAPI dependency to extract pagination parameters
    from query parameters.
    """
    
    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number"),
        limit: int = Query(20, ge=1, le=100, description="Items per page"),
        sort: Optional[str] = Query(None, description="Sort field"),
        order: Optional[str] = Query("asc", description="Sort order (asc or desc)")
    ):
        """
        Initialize pagination parameters.
        
        Args:
            page: Page number (1-based)
            limit: Items per page
            sort: Field to sort by
            order: Sort order (asc or desc)
        """
        self.page = page
        self.limit = limit
        self.sort = sort
        self.order = order
        
        # Calculate skip value for database queries
        self.skip = (page - 1) * limit


class PageInfo(BaseModel):
    """
    Page information for paginated responses.
    """
    current_page: int
    total_pages: int
    page_size: int
    total_items: int
    has_previous: bool
    has_next: bool


T = TypeVar('T')

class PaginatedResponse(BaseModel, Generic[T]):
    """
    Generic paginated response model.
    
    This class is used to standardize the format of paginated responses
    across all API endpoints.
    """
    items: List[T]
    page_info: PageInfo
    
    class Config:
        """Pydantic config."""
        arbitrary_types_allowed = True


def paginate_response(
    items: List[Any],
    total: int,
    pagination: PaginationParams
) -> Dict[str, Any]:
    """
    Create a standardized paginated response.
    
    Args:
        items: List of items for the current page
        total: Total number of items across all pages
        pagination: Pagination parameters
        
    Returns:
        Dict: Standardized response with items and pagination info
    """
    # Calculate pagination values
    total_pages = (total + pagination.limit - 1) // pagination.limit
    
    # Create page info
    page_info = PageInfo(
        current_page=pagination.page,
        total_pages=total_pages,
        page_size=pagination.limit,
        total_items=total,
        has_previous=pagination.page > 1,
        has_next=pagination.page < total_pages
    )
    
    # Create response
    return {
        "items": items,
        "page_info": page_info
    }


def get_pagination_links(
    path: str,
    pagination: PaginationParams,
    total: int,
    query_params: Optional[Dict[str, Any]] = None
) -> Dict[str, Optional[str]]:
    """
    Generate pagination links for HATEOAS.
    
    Args:
        path: Base path for links
        pagination: Pagination parameters
        total: Total number of items
        query_params: Additional query parameters
        
    Returns:
        Dict: Links for first, prev, next, and last pages
    """
    # Calculate pagination values
    total_pages = (total + pagination.limit - 1) // pagination.limit
    
    # Initialize query params
    params = query_params.copy() if query_params else {}
    
    # Helper to create URL with query params
    def create_url(page: int) -> str:
        page_params = {**params, "page": page, "limit": pagination.limit}
        
        if pagination.sort:
            page_params["sort"] = pagination.sort
            page_params["order"] = pagination.order
            
        query_string = "&".join(f"{key}={value}" for key, value in page_params.items())
        return f"{path}?{query_string}"
    
    # Create links
    links = {
        "first": create_url(1),
        "last": create_url(total_pages) if total_pages > 0 else None,
        "prev": create_url(pagination.page - 1) if pagination.page > 1 else None,
        "next": create_url(pagination.page + 1) if pagination.page < total_pages else None
    }
    
    return links